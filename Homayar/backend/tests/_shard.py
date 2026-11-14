"""Split the backend test files into CI parts (HOM-292).

CI runs the suite as a matrix of part jobs, each on its own runner, and passes
``--test-shard=<index>/<count>`` (for example ``1/4``): the run keeps the tests
of the files whose part is ``index``.  Without the option every test runs, as
before.  It is an option, not an environment variable, so a nested pytest run
that a test starts (the golden-module budget, the audit runtime guard) is not
sharded.

The part of a file comes from a duration plan over the checked-in timing map
(``shard_timings.json``, measured seconds per file, refreshed by the CI
artifacts through the ``merge`` command of this module).  The plan is a pure
function of the count and the map, never of what a run collects, so the part
jobs and every xdist worker of a run agree on each file without talking to
each other and every test runs in exactly one part.  A file missing from the
map falls back to the CRC-32 shard of its name, so a new file needs no map
update; the drift check in ``test_shard_assignment.py`` says when the map must
be refreshed.
"""

from __future__ import annotations

import argparse
import json
import sys
import zlib
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

SHARD_OPTION = "--test-shard"
# Chosen so that two shards get about the same work: with the per-file times of CI run
# 40814581688 and of a local -n 4 run, 50.0 and 50.1 % for the larger shard (without a
# prefix: 60.1 and 52.8 %).  Changing it moves files between the CI jobs.
SHARD_PREFIX = "hom292-118:"

TIMINGS_PATH = Path(__file__).with_name("shard_timings.json")
# Drift limit: how many collected files may be missing from the timing map before the
# map must be refreshed (the fallback still places them, but the parts stop being timed).
MAX_MISSING_FILES = 25

# One plan per (count, map content): collection asks for every item, the map is small.
_PLAN_CACHE: dict[tuple[int, tuple[tuple[str, float], ...]], dict[str, int]] = {}


def shard_of(file_name: str, count: int) -> int:
    """The shard (1 to ``count``) of the test file ``file_name`` (a name, not a path)."""
    if count < 1:
        raise ValueError(f"a shard count must be at least 1, got {count}")
    return zlib.crc32((SHARD_PREFIX + file_name).encode("utf-8")) % count + 1


def parse_shard(value: str | None) -> tuple[int, int] | None:
    """``(index, count)`` from the ``--test-shard`` value, or ``None`` when it is not given."""
    value = (value or "").strip()
    if not value:
        return None
    index_text, separator, count_text = value.partition("/")
    try:
        index, count = int(index_text), int(count_text)
    except ValueError:
        index = count = 0
    if not separator or count < 1 or not 1 <= index <= count:
        raise ValueError(
            f"{SHARD_OPTION} must be <index>/<count> with 1 <= index <= count, got {value!r}"
        )
    return index, count


def load_timings(path: Path = TIMINGS_PATH) -> dict[str, float]:
    """The "seconds" object of the map file: test file name -> seconds.

    ``{}`` when the file is absent, so a checkout without the map still runs (every
    file then takes the CRC fallback).
    """
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return {name: float(seconds) for name, seconds in content["seconds"].items()}


def part_of(file_name: str, count: int, timings: Mapping[str, float]) -> int:
    """The part (1..count) of a test file name.

    A name in ``timings``: the part the duration plan gives it.  A name not in
    ``timings``: ``shard_of(file_name, count)`` (the CRC fallback).  ``count < 1``
    raises ``ValueError``.
    """
    if count < 1:
        raise ValueError(f"a part count must be at least 1, got {count}")
    if file_name in timings:
        return _duration_plan(count, timings)[file_name]
    return shard_of(file_name, count)


def drift(file_names: Iterable[str], timings: Mapping[str, float]) -> tuple[list[str], list[str]]:
    """(stale, missing), both sorted: stale = names in the map that are not in file_names;
    missing = names in file_names that are not in the map."""
    names = set(file_names)
    stale = sorted(name for name in timings if name not in names)
    missing = sorted(name for name in names if name not in timings)
    return stale, missing


def _duration_plan(count: int, timings: Mapping[str, float]) -> dict[str, int]:
    """Name -> part: seconds descending then name ascending, each entry to the part with
    the smallest total so far, ties to the lowest part number."""
    key = (count, tuple(sorted(timings.items())))
    cached = _PLAN_CACHE.get(key)
    if cached is not None:
        return cached
    totals = [0.0] * count
    plan: dict[str, int] = {}
    for name, seconds in sorted(timings.items(), key=lambda item: (-item[1], item[0])):
        part = min(range(count), key=totals.__getitem__)
        totals[part] += seconds
        plan[name] = part + 1
    _PLAN_CACHE[key] = plan
    return plan


def merge_timings(out_path: Path, input_paths: Sequence[Path], source: str) -> None:
    """Write the timing map built from ``--shard-timings-out`` input files.

    A file name present in two inputs is an error (parts never share a file).
    """
    seconds: dict[str, float] = {}
    for input_path in input_paths:
        for name, value in json.loads(input_path.read_text(encoding="utf-8"))["seconds"].items():
            if name in seconds:
                raise ValueError(
                    f"{name} appears in more than one input ({input_path}); "
                    "parts never share a file"
                )
            seconds[name] = float(value)
    timings = {
        "source": source,
        "seconds": {name: round(seconds[name], 1) for name in sorted(seconds)},
    }
    out_path.write_text(json.dumps(timings, indent=2) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.tests._shard",
        description="Build and check the backend CI timing map (HOM-292)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    merge = commands.add_parser("merge", help="merge --shard-timings-out files into a timing map")
    merge.add_argument("out", type=Path, help="the timing map to write")
    merge.add_argument("inputs", nargs="+", type=Path, help="--shard-timings-out files")
    merge.add_argument(
        "--source",
        default=None,
        help="free text for the source field, e.g. the CI run ids (default: the input file names)",
    )
    args = parser.parse_args(argv)
    try:
        # Base names only: the map is checked in, and a local folder must not go with it.
        source = args.source or ", ".join(path.name for path in args.inputs)
        merge_timings(args.out, args.inputs, source)
    except ValueError as exc:
        print(f"merge: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
