"""CI shards (HOM-292, ``_shard.py``): every test runs in exactly one shard.

The shard of a test file is a pure function of its name, so a new file needs no list
update; without ``--test-shard`` every test runs.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from backend.tests._shard import SHARD_OPTION, parse_shard, shard_of

TESTS = Path(__file__).resolve().parent
HOMAYAR_ROOT = TESTS.parents[1]


def _test_files() -> list[str]:
    return sorted(path.name for path in TESTS.glob("test_*.py"))


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


def _collected(shard: str | None) -> set[str]:
    """Node ids a ``--collect-only`` run collects from three files, with this shard."""
    files = ["test_api.py", "test_speech_normalize.py", "test_shard_assignment.py"]
    option = [SHARD_OPTION, shard] if shard is not None else []
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


def test_the_two_shards_split_the_collection_and_together_are_the_whole():
    everything = _collected(None)
    first, second = _collected("1/2"), _collected("2/2")
    assert everything
    assert first.isdisjoint(second)
    assert first | second == everything
    for shard, nodeids in ((1, first), (2, second)):
        assert {nodeid.split("::")[0].rsplit("/", 1)[-1] for nodeid in nodeids} == {
            name
            for name in ("test_api.py", "test_speech_normalize.py", "test_shard_assignment.py")
            if shard_of(name, 2) == shard
        }
