"""Runs the corpus through both sides and prints the gate table (HOM-321).

The base side is either a git revision exported with ``git archive`` (removed
when the run ends, also on failure) or a folder given with ``--base-dir``.
Each side runs in its own worker subprocess with ``PYTHONPATH`` and the
working directory set to the side root (SPEC 3.3), base first, then patch.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from backend.tests.tools.privacy_gates import corpus as corpus_mod
from backend.tests.tools.privacy_gates import gates as gates_mod
from backend.tests.tools.privacy_gates.gates import SetupError

# The Homayar/ folder that holds this tool (backend/tests/tools/privacy_gates).
TOOL_TREE = Path(__file__).resolve().parents[4]


def _git(tree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed git arguments, repositories the developer owns
        ["git", "-C", str(tree), *args],  # noqa: S607 - git from PATH
        capture_output=True,
        text=True,
    )


def _prefix() -> str:
    """The path of the tool's tree dir relative to the git top level."""
    done = _git(TOOL_TREE, "rev-parse", "--show-prefix")
    if done.returncode != 0:
        raise SetupError(f"the tree that holds this tool is not a git work tree: {TOOL_TREE}")
    return done.stdout.strip().strip("/")


def _toplevel() -> Path:
    done = _git(TOOL_TREE, "rev-parse", "--show-toplevel")
    if done.returncode != 0:
        raise SetupError(f"the tree that holds this tool is not a git work tree: {TOOL_TREE}")
    return Path(done.stdout.strip())


def _export_base(rev: str, target: Path) -> tuple[Path, str]:
    """Export ``<prefix>/backend`` of the revision into target; return the side
    root (the folder that holds backend/) and a label for the header.  Every
    git command runs in the repository that holds this tool, never in
    ``--tree-dir``, which may be any folder (SPEC 3.1)."""
    resolved = _git(TOOL_TREE, "rev-parse", "--verify", f"{rev}^{{commit}}")
    if resolved.returncode != 0:
        raise SetupError(f"revision does not resolve to a commit: {rev}")
    full_sha = resolved.stdout.strip()
    prefix = _prefix()
    path = f"{prefix}/backend" if prefix else "backend"
    archive_command = [
        "git",
        "-C",
        str(_toplevel()),
        "archive",
        "--format=tar",
        full_sha,
        "--",
        path,
    ]
    done = subprocess.run(  # noqa: S603, S607 - fixed git arguments, git from PATH
        archive_command,
        capture_output=True,
    )
    if done.returncode != 0:
        raise SetupError(f"git archive failed for {rev}")
    try:
        with tarfile.open(fileobj=io.BytesIO(done.stdout), mode="r:") as archive:
            archive.extractall(target, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise SetupError(f"cannot extract the git archive of {rev}: {exc}") from exc
    side_root = target / prefix if prefix else target
    if not (side_root / "backend" / "privacy" / "text_pii.py").is_file():
        raise SetupError(f"revision {rev} has no {path}/privacy/text_pii.py")
    return side_root, f"{rev} ({full_sha})"


def _run_worker(
    side: str, side_root: Path, corpus_dir: Path, out_dir: Path, cost_rows: int
) -> dict:
    """Run worker.py inside the side's tree; return its results (SPEC 3.3)."""
    here = Path(__file__).resolve().parent
    worker = here / "worker.py"
    cost_module = here.parents[1] / "_cost.py"  # the working tree's backend/tests/_cost.py
    command = [
        sys.executable,
        "-P",
        str(worker),
        "--side",
        side,
        "--side-root",
        str(side_root),
        "--corpus-dir",
        str(corpus_dir),
        "--out-dir",
        str(out_dir / side),
        "--cost-rows",
        str(cost_rows),
        "--cost-module",
        str(cost_module),
    ]
    env = dict(os.environ, PYTHONPATH=str(side_root), PYTHONHASHSEED="0")
    done = subprocess.run(  # noqa: S603 - this interpreter running our own worker
        command, cwd=str(side_root), env=env, capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        lines = [line for line in done.stderr.strip().splitlines() if line.strip()]
        detail = lines[-1] if lines else "no stderr"
        raise SetupError(f"{side} worker failed (exit {done.returncode}): {detail}")
    results_path = out_dir / side / "results.json"
    if not results_path.is_file():
        raise SetupError(f"{side} worker wrote no results file")
    return json.loads(results_path.read_text(encoding="utf-8"))


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, not {number}")
    return number


def cmd_corpus(args: argparse.Namespace) -> int:
    try:
        corpus_dir = corpus_mod.write_corpus(Path(args.out), args.seed, args.rows)
    except (OSError, ValueError) as exc:
        raise SetupError(f"cannot write the corpus to {args.out}: {exc}") from exc
    print(corpus_dir)
    return 0


def _setup(args: argparse.Namespace, tree_dir: Path) -> tuple:
    """Prepare the base side, the out dir, the corpus and both worker results.

    The base side is prepared and checked first, so a bad revision or a bad
    base dir fails before the corpus is written (no wasted work).  An OSError
    or ValueError along the way (a bad --out path, an unreadable corpus,
    broken worker output) becomes a SetupError, so it exits 2 instead of 1,
    which a CI step would read as a gate failure."""
    base_tmp: Path | None = None
    try:
        if args.base is not None:
            base_tmp = Path(tempfile.mkdtemp(prefix="privacy-gates-base-"))
            base_root, base_label = _export_base(args.base, base_tmp)
        else:
            base_root = Path(args.base_dir).resolve()
            if not (base_root / "backend" / "privacy" / "text_pii.py").is_file():
                raise SetupError(f"base dir has no backend/privacy/text_pii.py: {base_root}")
            base_label = str(base_root)
        if args.out:
            out_dir = Path(args.out).resolve()
        else:
            out_dir = Path(tempfile.mkdtemp(prefix="privacy-gates-"))
        out_dir.mkdir(parents=True, exist_ok=True)
        corpus_dir = corpus_mod.write_corpus(out_dir, args.seed, args.rows)
        base_results = _run_worker("base", base_root, corpus_dir, out_dir, args.cost_rows)
        patch_results = _run_worker("patch", tree_dir, corpus_dir, out_dir, args.cost_rows)
        return base_results, patch_results, base_label, out_dir, corpus_dir
    except (OSError, ValueError) as exc:
        raise SetupError(str(exc)) from exc
    finally:
        if base_tmp is not None:
            shutil.rmtree(base_tmp, ignore_errors=True)


def cmd_run(args: argparse.Namespace) -> int:
    if bool(args.base) == bool(args.base_dir):
        raise SetupError("exactly one of --base or --base-dir is required")
    tree_dir = Path(args.tree_dir).resolve() if args.tree_dir else TOOL_TREE
    base_results, patch_results, base_label, out_dir, corpus_dir = _setup(args, tree_dir)

    rows_by_corpus = corpus_mod.read_corpus(corpus_dir)
    report = gates_mod.evaluate(rows_by_corpus, base_results, patch_results, args.cost_limit)
    table = gates_mod.format_table(report["table"])
    header = [
        "privacy gates (HOM-321)",
        f"base: {base_label}",
        f"tree: {tree_dir}",
        f"seed: {args.seed}  rows: {args.rows}  cost rows: {args.cost_rows}"
        f"  cost limit: {args.cost_limit:g}",
        "detector version:"
        f" base {base_results['meta'].get('detector_version')}"
        f"  patch {patch_results['meta'].get('detector_version')}",
        f"out: {out_dir}",
    ]
    verdict = gates_mod.result_line(report)
    print("\n".join(header))
    print()
    print("\n".join(table))
    print(verdict)

    machine_report = {
        "header": {
            "base": base_label,
            "tree": str(tree_dir),
            "seed": args.seed,
            "rows": args.rows,
            "cost_rows": args.cost_rows,
            "cost_limit": args.cost_limit,
            "out": str(out_dir),
            "base_meta": base_results["meta"],
            "patch_meta": patch_results["meta"],
        },
        "result": report["result"],
        "failed_gates": report["failed_gates"],
        "gates": report["gates"],
        "table": [list(map(str, row)) for row in report["table"]],
    }
    (out_dir / "report.json").write_text(
        json.dumps(machine_report, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    markdown = [
        "# Privacy gates (HOM-321)",
        "",
        *header[1:],
        "",
        *gates_mod.markdown_table(report["table"]),
        "",
        verdict,
        "",
    ]
    (out_dir / "report.md").write_text("\n".join(markdown), encoding="utf-8")
    return 0 if report["result"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.tests.tools.privacy_gates",
        description="Privacy gate harness (HOM-321): corpora, gate runner, reports.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="run the corpus on a base and on the tree, compare")
    run_parser.add_argument("--base", help="git revision whose <prefix>/backend is the base side")
    run_parser.add_argument("--base-dir", help="folder that holds backend/privacy/text_pii.py")
    run_parser.add_argument(
        "--tree-dir",
        help="patch side folder (default: the Homayar tree that holds this tool)",
    )
    run_parser.add_argument("--seed", type=int, default=321, help="corpus seed")
    run_parser.add_argument("--rows", type=_positive_int, default=20000, help="rows per corpus")
    run_parser.add_argument(
        "--cost-rows", type=_positive_int, default=500, help="rows per cost workload"
    )
    run_parser.add_argument("--cost-limit", type=float, default=1.20, help="G5 patch/base limit")
    run_parser.add_argument("--out", help="output folder (default: a new temp folder)")

    corpus_parser = sub.add_parser("corpus", help="write only the corpus files")
    corpus_parser.add_argument("--out", required=True, help="output folder")
    corpus_parser.add_argument("--seed", type=int, default=321, help="corpus seed")
    corpus_parser.add_argument("--rows", type=_positive_int, default=20000, help="rows per corpus")

    args = parser.parse_args()
    try:
        if args.command == "corpus":
            return cmd_corpus(args)
        return cmd_run(args)
    except SetupError as exc:
        print(f"setup error: {exc}", file=sys.stderr)
        return 2
