"""Periodic sweeps reach every workspace (HOM-210).

``discover_workspaces`` used a fixed ``LIMIT``, so the retention purge, media
reconciliation, invariants and artifact cleanup never visited a workspace
whose id sorted after the first window.  ``sweep_window`` is the pure part of
the fix and is tested here without a database.
"""

from __future__ import annotations

import pytest

from backend.jobs.checkpoints import sweep_window

IDS = [f"ws-{index:04d}" for index in range(250)]


def test_consecutive_runs_cover_every_workspace_once_per_cycle():
    seen: list[str] = []
    for run in range(3):  # ceil(250 / 100) runs make one cycle
        seen.extend(sweep_window(IDS, limit=100, run=run))
    assert sorted(seen) == IDS


@pytest.mark.parametrize("start_run", [0, 7, 1234567])
def test_any_cycle_covers_every_workspace(start_run):
    windows = -(-len(IDS) // 100)
    seen = set()
    for run in range(start_run, start_run + windows):
        seen.update(sweep_window(IDS, limit=100, run=run))
    assert seen == set(IDS)


def test_a_window_never_exceeds_the_limit():
    for run in range(10):
        assert len(sweep_window(IDS, limit=100, run=run)) <= 100


def test_the_start_inside_a_window_rotates_so_one_busy_workspace_cannot_starve_its_neighbours():
    windows = -(-len(IDS) // 100)
    firsts = {sweep_window(IDS, limit=100, run=cycle * windows)[0] for cycle in range(100)}
    assert firsts == set(IDS[:100])


def test_a_small_deployment_is_swept_whole_every_run():
    ids = ["a", "b", "c"]
    for run in range(5):
        assert sorted(sweep_window(ids, limit=100, run=run)) == ids


def test_no_workspaces_or_no_budget_gives_an_empty_window():
    assert sweep_window([], limit=100, run=3) == []
    assert sweep_window(IDS, limit=0, run=3) == []
