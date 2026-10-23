"""HOM-216: a stored future timestamp in the freshness gauge is dropped, not trusted.

A skewed clock (or a hand edit) can leave ``homayar_backup_last_success_timestamp_seconds``
holding a Unix time in the future.  Max-merge alone would keep it forever and the
age alert would never fire again.  These tests pin the drop, the allowance boundary,
the unchanged max-merge for past values, the log line, and the wall-clock default.
"""

from __future__ import annotations

import logging
import time

from backend.backup import metrics

NOW = 1_793_000_000.0  # a fixed Unix time: integer-valued, so .3f round-trips exactly


def _stored(path, **values: float) -> None:
    """Seed the textfile as a previous run (or a hand edit) left it."""
    lines = [f"# TYPE {metrics.METRIC} gauge"]
    lines += [f'{metrics.METRIC}{{target="{t}"}} {v:.3f}' for t, v in values.items()]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def _warnings(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "backend.backup.metrics" and record.levelno == logging.WARNING
    ]


def test_a1_stored_future_value_is_dropped_for_the_written_target(tmp_path):
    path = tmp_path / "homayar_backup.prom"
    _stored(path, postgres=NOW + 3600)
    metrics.record_success(path, {"postgres": NOW - 10}, now=NOW)
    assert metrics.read_values(path) == {"postgres": NOW - 10}


def test_a2_stored_future_value_of_an_unwritten_target_loses_its_line(tmp_path):
    path = tmp_path / "homayar_backup.prom"
    _stored(path, postgres=NOW - 100, objects=NOW + 3600)
    metrics.record_success(path, {"postgres": NOW - 10}, now=NOW)
    values = metrics.read_values(path)
    assert "objects" not in values and values == {"postgres": NOW - 10}


def test_a3_a_value_inside_the_allowance_is_kept_by_the_max_merge(tmp_path, caplog):
    path = tmp_path / "homayar_backup.prom"
    _stored(path, postgres=NOW + 60)
    with caplog.at_level(logging.WARNING, logger="backend.backup.metrics"):
        metrics.record_success(path, {"postgres": NOW - 10}, now=NOW)
    assert metrics.read_values(path) == {"postgres": NOW + 60}
    assert _warnings(caplog) == []


def test_a4_the_boundary_is_kept_one_second_beyond_it_is_dropped(tmp_path):
    assert metrics.SKEW_ALLOWANCE_SECONDS == 300.0
    path = tmp_path / "homayar_backup.prom"
    _stored(path, postgres=NOW + metrics.SKEW_ALLOWANCE_SECONDS)
    metrics.record_success(path, {"postgres": NOW - 10}, now=NOW)
    assert metrics.read_values(path) == {"postgres": NOW + metrics.SKEW_ALLOWANCE_SECONDS}
    _stored(path, postgres=NOW + metrics.SKEW_ALLOWANCE_SECONDS + 1)
    metrics.record_success(path, {"postgres": NOW - 10}, now=NOW)
    assert metrics.read_values(path) == {"postgres": NOW - 10}


def test_a5_a_normal_past_value_still_max_merges(tmp_path, caplog):
    path = tmp_path / "homayar_backup.prom"
    _stored(path, postgres=NOW - 50)
    with caplog.at_level(logging.WARNING, logger="backend.backup.metrics"):
        metrics.record_success(path, {"postgres": NOW - 100}, now=NOW)
        assert metrics.read_values(path) == {"postgres": NOW - 50}
        metrics.record_success(path, {"postgres": NOW - 10}, now=NOW)
    assert metrics.read_values(path) == {"postgres": NOW - 10}
    assert _warnings(caplog) == []


def test_a6_a_drop_logs_one_warning_per_target_without_the_path(tmp_path, caplog):
    path = tmp_path / "textfile-dir" / "homayar_backup.prom"
    _stored(path, postgres=NOW - 100, objects=NOW + 3600, offsite=NOW + 7200)
    with caplog.at_level(logging.WARNING, logger="backend.backup.metrics"):
        metrics.record_success(path, {"postgres": NOW - 10}, now=NOW)
    warnings = _warnings(caplog)
    assert len(warnings) == 2
    assert sum("objects" in message for message in warnings) == 1
    assert sum("offsite" in message for message in warnings) == 1
    # The dropped value and now appear in full Unix-time form (the .3f of the textfile).
    assert any(f"{NOW + 3600:.3f}" in message for message in warnings)
    assert any(f"{NOW + 7200:.3f}" in message for message in warnings)
    assert any(f"{NOW:.3f}" in message for message in warnings)
    assert all(str(tmp_path) not in message for message in warnings)
    assert all(str(path) not in message and str(path.parent) not in message for message in warnings)
    assert metrics.read_values(path) == {"postgres": NOW - 10}


def test_a7_without_now_the_wall_clock_drops_a_future_value(tmp_path):
    path = tmp_path / "homayar_backup.prom"
    now = float(int(time.time()))
    _stored(path, postgres=now + 3600)
    metrics.record_success(path, {"postgres": now - 10})
    assert metrics.read_values(path) == {"postgres": now - 10}
