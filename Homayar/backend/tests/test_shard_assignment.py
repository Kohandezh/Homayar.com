"""CI parts (HOM-292, ``_shard.py``): every test runs in exactly one part.

The part of a test file comes from a duration plan over the checked-in timing
map (``shard_timings.json``); a file missing from the map falls back to the
CRC-32 shard of its name, so a new file needs no map update and without
``--test-shard`` every test runs.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.tests._parallel import largest_files_first
from backend.tests._shard import (
    MAX_MISSING_FILES,
    SHARD_OPTION,
    TIMINGS_PATH,
    drift,
    load_timings,
    parse_shard,
    part_of,
    shard_of,
)

TESTS = Path(__file__).resolve().parent
HOMAYAR_ROOT = TESTS.parents[1]

# A five-entry map with its plan pinned by hand.  The plan takes the entries by
# seconds, largest first, and gives each one to the part with the smallest total so
# far (ties to the lowest part number): a 8.0 to part 1 (both empty), b 6.0 to part
# 2, c 4.0 to part 2 (6 < 8), d 3.5 to part 1 (8 < 10), e 3.4 to part 2 (10 < 11.5).
PINNED_MAP = {
    "test_a.py": 8.0,
    "test_b.py": 6.0,
    "test_c.py": 4.0,
    "test_d.py": 3.5,
    "test_e.py": 3.4,
}

# What a failing map check prints: how to rebuild the map from the CI artifacts.
REFRESH_HINT = (
    "Refresh the map: download the artifacts backend-shard-timings-part-1 to "
    "backend-shard-timings-part-4 of one green CI run, then in Homayar/ run "
    "`uv run python -m backend.tests._shard merge backend/tests/shard_timings.json "
    "shard-timings-part-1.json shard-timings-part-2.json shard-timings-part-3.json "
    'shard-timings-part-4.json --source "CI run <id>"` and commit the result.'
)


def _test_files() -> list[str]:
    return sorted(path.name for path in TESTS.glob("test_*.py"))


def _collected_names() -> list[str]:
    """Names a collection can see: every ``test_*.py`` under ``backend/tests``."""
    return sorted(path.name for path in TESTS.rglob("test_*.py"))


def test_every_test_file_has_exactly_one_shard_for_each_count():
    files = _test_files()
    for count in (1, 2, 3, 4):
        shards = {name: shard_of(name, count) for name in files}
        assert set(shards.values()) <= set(range(1, count + 1))
        assert sum(
            sum(1 for shard in shards.values() if shard == index) for index in range(1, count + 1)
        ) == len(files)


def test_the_shard_is_a_pure_function_of_the_file_name():
    # A new file needs no list: its name alone gives its shard.  Pinned values: changing
    # the function moves files between CI jobs, which should be a visible decision.
    assert {
        name: shard_of(name, 2)
        for name in ("test_api.py", "test_speech_normalize.py", "test_a_new_file.py")
    } == {
        "test_api.py": 2,
        "test_speech_normalize.py": 1,
        "test_a_new_file.py": 2,
    }


def test_both_shards_have_tests():
    files = _test_files()
    assert {shard_of(name, 2) for name in files} == {1, 2}


def test_the_shard_option_is_read_and_checked():
    assert parse_shard(None) is None
    assert parse_shard("") is None
    assert parse_shard(" 2/2 ") == (2, 2)
    for wrong in ("0/2", "3/2", "a/b", "2", "1/0", "/2"):
        with pytest.raises(ValueError, match=SHARD_OPTION):
            parse_shard(wrong)


def test_every_test_file_gets_exactly_one_part_for_each_count():
    # The plan and the fallback are functions of the file name, so whatever the
    # map holds, each collected file lands in one part and (for count 4, today's
    # CI shape) no part stays empty.
    names = _collected_names()
    timings = load_timings()
    for count in (1, 2, 3, 4):
        for name in names:
            assert 1 <= part_of(name, count, timings) <= count, (name, count)
    assert {part_of(name, 4, timings) for name in names} == {1, 2, 3, 4}


def test_test_file_names_are_unique_across_backend_tests():
    # The plan is keyed by file name alone, so two files with the same name in
    # different folders would silently share one part.
    names = _collected_names()
    assert len(names) == len(set(names))


def test_part_of_follows_the_plan_and_falls_back_to_the_shard():
    assert {name: part_of(name, 2, PINNED_MAP) for name in PINNED_MAP} == {
        "test_a.py": 1,
        "test_b.py": 2,
        "test_c.py": 2,
        "test_d.py": 1,
        "test_e.py": 2,
    }
    # Equal seconds: the earlier name goes to the lowest-numbered empty part.
    assert part_of("test_x.py", 2, {"test_x.py": 5.0, "test_y.py": 5.0}) == 1
    assert part_of("test_y.py", 2, {"test_x.py": 5.0, "test_y.py": 5.0}) == 2
    # A name outside the map keeps the CRC fallback, whatever the count.
    for count in (1, 2, 3, 4):
        assert part_of("test_not_in_the_map.py", count, PINNED_MAP) == shard_of(
            "test_not_in_the_map.py", count
        )
    with pytest.raises(ValueError, match="part count"):
        part_of("test_a.py", 0, PINNED_MAP)


def test_the_checked_in_map_matches_the_test_files():
    # A stale entry (a renamed or deleted file) must leave the map.  New files use the
    # CRC fallback, but beyond MAX_MISSING_FILES of them the parts are no longer timed.
    stale, missing = drift(_collected_names(), load_timings())
    assert stale == [], f"{TIMINGS_PATH} names files that no longer exist: {stale}. {REFRESH_HINT}"
    assert len(missing) <= MAX_MISSING_FILES, (
        f"{len(missing)} test files are not in {TIMINGS_PATH} "
        f"(limit {MAX_MISSING_FILES}). {REFRESH_HINT}"
    )


def test_the_checked_in_map_balances_the_four_parts():
    # The balance guard: with the plan over the real map, the busiest part is
    # at most 1.10 times the quietest one.  An empty map has only fallback
    # files and fails here on purpose.
    timings = load_timings()
    totals = [0.0] * 4
    for name, seconds in timings.items():
        totals[part_of(name, 4, timings) - 1] += seconds
    assert min(totals) > 0, (
        f"{TIMINGS_PATH} gives a part no seconds, so the four parts are not timed. {REFRESH_HINT}"
    )
    assert max(totals) <= 1.10 * min(totals), f"part totals out of balance: {totals}"


def test_the_merge_command_merges_inputs_and_rejects_a_shared_name(tmp_path):
    first = tmp_path / "part-1.json"
    first.write_text(
        json.dumps({"seconds": {"test_b.py": 6.0, "test_c.py": 4.04}}), encoding="utf-8"
    )
    second = tmp_path / "part-2.json"
    second.write_text(json.dumps({"seconds": {"test_a.py": 8.4}}), encoding="utf-8")
    out = tmp_path / "timings.json"
    merged = subprocess.run(  # noqa: S603 - a fixed module command line
        [
            sys.executable,
            "-m",
            "backend.tests._shard",
            "merge",
            str(out),
            str(first),
            str(second),
            "--source",
            "CI runs 1 and 2",
        ],
        cwd=HOMAYAR_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert merged.returncode == 0, merged.stdout + merged.stderr
    text = out.read_text(encoding="utf-8")
    assert json.loads(text) == {
        "source": "CI runs 1 and 2",
        "seconds": {"test_a.py": 8.4, "test_b.py": 6.0, "test_c.py": 4.0},
    }
    assert text.endswith("}\n") and '\n  "seconds"' in text, text

    # Parts never share a file: a name in two inputs is a merge error.
    clash = tmp_path / "part-3.json"
    clash.write_text(json.dumps({"seconds": {"test_c.py": 1.0}}), encoding="utf-8")
    rejected = subprocess.run(  # noqa: S603 - a fixed module command line
        [sys.executable, "-m", "backend.tests._shard", "merge", str(out), str(first), str(clash)],
        cwd=HOMAYAR_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert rejected.returncode == 2
    assert "test_c.py" in rejected.stderr, rejected.stderr


def test_the_scheduler_hands_out_the_largest_files_first():
    # Under ``--dist loadfile`` xdist hands out whole files.  The slowest files of a
    # part start first, so none of them ends the run alone; files the map does not
    # know keep xdist's own order after them, even after a 0.0 s entry.
    queue = [
        "backend/tests/test_new_one.py",
        "backend/tests/test_c.py",
        "backend/tests/test_zero.py",
        "backend/tests/test_new_two.py",
        "backend/tests/test_a.py",
        "backend/tests/test_b.py",
    ]
    timings = {"test_a.py": 8.0, "test_b.py": 6.0, "test_c.py": 4.0, "test_zero.py": 0.0}
    assert largest_files_first(queue, timings) == [
        "backend/tests/test_a.py",
        "backend/tests/test_b.py",
        "backend/tests/test_c.py",
        "backend/tests/test_zero.py",
        "backend/tests/test_new_one.py",
        "backend/tests/test_new_two.py",
    ]
    assert largest_files_first(queue, {}) == queue


def _collected(part: str | None) -> set[str]:
    """Node ids a ``--collect-only`` run collects from three files, with this part."""
    files = ["test_api.py", "test_speech_normalize.py", "test_shard_assignment.py"]
    option = [SHARD_OPTION, part] if part is not None else []
    result = subprocess.run(  # noqa: S603 - a fixed pytest command line
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"]
        + option
        + [f"backend/tests/{name}" for name in files],
        cwd=HOMAYAR_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode in (0, 5), result.stdout[-3000:] + result.stderr[-3000:]
    return {line for line in result.stdout.splitlines() if "::" in line}


def test_the_four_parts_split_the_collection_and_together_are_the_whole():
    everything = _collected(None)
    parts = [_collected(f"{index}/4") for index in (1, 2, 3, 4)]
    assert everything
    for part in parts:
        assert part <= everything
    union: set[str] = set()
    for part in parts:
        assert part.isdisjoint(union)
        union |= part
    assert union == everything
    timings = load_timings()
    files = ["test_api.py", "test_speech_normalize.py", "test_shard_assignment.py"]
    for index, nodeids in enumerate(parts, start=1):
        assert {nodeid.split("::")[0].rsplit("/", 1)[-1] for nodeid in nodeids} == {
            name for name in files if part_of(name, 4, timings) == index
        }
