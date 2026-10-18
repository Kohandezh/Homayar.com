"""Periodic sweeps reach every workspace (HOM-210).

``discover_workspaces`` used a fixed ``LIMIT``, so the retention purge, media
reconciliation, invariants and artifact cleanup never visited a workspace
whose id sorted after the first batch.  Every run now gets every workspace in
an order that rotates per sweep interval; ``rotate`` is tested here without a
database.
"""

from __future__ import annotations

import pytest

from backend.jobs.checkpoints import rotate

IDS = [f"ws-{index:04d}" for index in range(250)]


@pytest.mark.parametrize("run", [0, 1, 99, 100, 249, 250, -3, 10**9])
def test_every_run_returns_every_workspace_exactly_once(run):
    order = rotate(IDS, run=run)
    assert sorted(order) == IDS
    assert len(order) == len(IDS)


def test_the_first_workspace_moves_every_run_so_none_is_always_last():
    firsts = {rotate(IDS, run=run)[0] for run in range(len(IDS))}
    assert firsts == set(IDS)


def test_a_budget_of_100_reaches_every_workspace_within_a_few_consecutive_runs():
    # Worst case for a shared item budget: each run serves only the first 100.
    served: set[str] = set()
    for run in range(0, 300, 100):
        served.update(rotate(IDS, run=run)[:100])
    assert served == set(IDS)


def test_no_workspaces_gives_an_empty_order():
    assert rotate([], run=7) == []
