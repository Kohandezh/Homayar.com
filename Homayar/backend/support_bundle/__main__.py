"""Operator CLI: ``python -m backend.support_bundle <command>`` (HOM-110).

  collect                    one JSON document of container-side facts on stdout
  scan DIR [--env-file F]    scan every file under DIR; exit 0 clean,
                             1 with one ``kind=/file=`` line per finding
                             (never the matched text), 2 usage error
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.support_bundle.collect import collect_document
from backend.support_bundle.scan import ScanError, find_secrets, scan_tree


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.support_bundle",
        description="Sanitized support-bundle facts and the fail-closed scanner",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("collect", help="print the container-side facts as one JSON document")
    scan_parser = commands.add_parser("scan", help="scan a directory for findings")
    scan_parser.add_argument("directory", type=Path, help="the directory to scan")
    scan_parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="an operator env file whose secret values must not appear in any file",
    )
    args = parser.parse_args(argv)

    if args.command == "collect":
        print(json.dumps(collect_document(), indent=2, sort_keys=True, ensure_ascii=False))
        return 0

    root: Path = args.directory
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2
    try:
        secrets = find_secrets(args.env_file)
        findings = scan_tree(root, secrets)
    except ScanError as exc:
        # Fail closed: an unreadable input must never report "clean".
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for finding in findings:
        print(f"finding kind={finding.kind} file={finding.path}")
    return 1 if findings else 0


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    sys.exit(main())
