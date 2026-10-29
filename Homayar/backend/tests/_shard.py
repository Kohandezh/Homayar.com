"""Split the backend test files into CI shards (HOM-292).

CI runs the suite as a matrix of jobs, each on its own runner, and passes
``--test-shard <index>/<count>`` (for example ``1/2``): the run keeps the tests of the
files whose shard is ``index``.  Without the option every test runs, as before.  It is an
option, not an environment variable, so a nested pytest run that a test starts (the
golden-module budget, the audit runtime guard) is not sharded.  The shard of a file is a
pure function of its file name (CRC-32 of a fixed prefix and the name, modulo the count), so
every test runs
in exactly one shard and a new test file lands in a shard with nothing to update.
"""

from __future__ import annotations

import zlib

SHARD_OPTION = "--test-shard"
# Chosen so that two shards get about the same work: with the per-file times of CI run
# 40814581688 and of a local -n 4 run, 50.0 and 50.1 % for the larger shard (without a
# prefix: 60.1 and 52.8 %).  Changing it moves files between the CI jobs.
SHARD_PREFIX = "hom292-118:"


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
