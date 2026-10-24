"""Cost measures for tests that bound how much work code does (HOM-310).

A limit in seconds (wall clock or CPU) measures how fast and how busy the
runner is, not the rule a test protects.  A slow CI runner then fails a test
whose code did not change (HOM-301, HOM-307).  This module gives two measures
that do not depend on the runner:

* ``lines_run`` counts the lines of Python code that one call runs in some
  modules.  The count is the same on every machine.
* ``cpu_units`` gives the CPU time of one call in units of a fixed calibration
  workload, measured in the same thread just before.  On a slower or busier
  runner both grow together, so the ratio stays about the same.

``WIDE_BOUND_SECONDS`` is the one upper time bound left in these tests.  It is
far above a loaded runner's overhead and catches only a real hang or blow-up.
"""

from __future__ import annotations

import importlib
import re
import sys
import time
import types
from collections.abc import Callable, Iterable
from typing import Any

import pytest

WIDE_BOUND_SECONDS = 10.0


def _code_objects(module_names: Iterable[str]) -> list[types.CodeType]:
    """Every code object of the modules: functions, methods (also static and
    class methods), and the code nested in them."""
    functions: list[types.FunctionType] = []
    for name in module_names:
        module = importlib.import_module(name)
        for obj in vars(module).values():
            if isinstance(obj, types.FunctionType) and obj.__module__ == name:
                functions.append(obj)
        for obj in vars(module).values():
            if isinstance(obj, type) and obj.__module__ == name:
                for member in vars(obj).values():
                    fn = (
                        member.__func__
                        if isinstance(member, (staticmethod, classmethod))
                        else member
                    )
                    if isinstance(fn, types.FunctionType) and fn.__module__ == name:
                        functions.append(fn)
    codes: list[types.CodeType] = []
    seen: set[types.CodeType] = set()
    stack = [fn.__code__ for fn in functions]
    while stack:
        code = stack.pop()
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)
        stack.extend(c for c in code.co_consts if isinstance(c, types.CodeType))
    return codes


class _StopCounting(BaseException):
    """Raised in the counted code at ``stop_at`` lines.  A BaseException, so an
    ``except Exception`` in that code does not catch it."""


def lines_run(
    module_names: tuple[str, ...],
    fn: Callable[..., Any],
    /,
    *args: Any,
    stop_at: int | None = None,
    **kwargs: Any,
) -> tuple[int, Any]:
    """Run ``fn(*args, **kwargs)`` once; return the lines of code of the named
    modules it ran, and its result.

    With ``stop_at``, ``fn`` is stopped when the count reaches it, and the
    result is None.  A test of a ceiling then does not wait for code that would
    run far past it.

    It uses Python 3.12 ``sys.monitoring`` LINE events on a free tool id (never
    the debugger or coverage id), and removes every event, the callback and the
    tool id afterwards, also when ``fn`` raises.
    """
    codes = _code_objects(module_names)
    tool = next(
        (
            candidate
            for candidate in (sys.monitoring.PROFILER_ID, 3, 4, sys.monitoring.OPTIMIZER_ID)
            if sys.monitoring.get_tool(candidate) is None
        ),
        None,
    )
    if tool is None:
        pytest.fail(f"no free sys.monitoring tool id to count lines of {module_names}")
    count = 0

    def counter(code: types.CodeType, line_number: int) -> None:
        nonlocal count
        count += 1
        if stop_at is not None and count >= stop_at:
            raise _StopCounting

    result = None
    sys.monitoring.use_tool_id(tool, "count lines run")
    try:
        sys.monitoring.register_callback(tool, sys.monitoring.events.LINE, counter)
        for code in codes:
            sys.monitoring.set_local_events(tool, code, sys.monitoring.events.LINE)
        result = fn(*args, **kwargs)
    except _StopCounting:
        pass
    finally:
        for code in codes:
            sys.monitoring.set_local_events(tool, code, 0)
        sys.monitoring.register_callback(tool, sys.monitoring.events.LINE, None)
        sys.monitoring.free_tool_id(tool)
    return count, result


# The calibration workload: fixed C-level regex and string work.
_CALIBRATION_TEXT = "homayar meeting 4521, " * 2000
_CALIBRATION_RE = re.compile(r"(\w+) (\w+) (\d+),")
_CALIBRATION_ROUNDS = 30
# Best of 3 on the owner's Mac (Apple M3, no other test run), Python 3.12.13,
# 2026-10-24: LOCAL_CALIBRATION_SECONDS = 0.013.  With other suites running on the
# same Mac it was 0.021 to 0.032: the test's own work slows the same way.  For the
# record only: no assertion uses it.


def _calibration() -> None:
    for _ in range(_CALIBRATION_ROUNDS):
        _CALIBRATION_RE.findall(_CALIBRATION_TEXT)
        _CALIBRATION_TEXT.upper().split(",")


def _thread_cpu(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> tuple[float, Any]:
    started = time.thread_time()  # this thread's CPU time: other threads are not counted
    result = fn(*args, **kwargs)
    return time.thread_time() - started, result


def cpu_units(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> tuple[float, Any]:
    """Run ``fn(*args, **kwargs)`` once; return its CPU time in calibration
    units, and its result.

    One unit is the best of 3 runs of the calibration workload in this thread,
    measured just before.  A budget in units means the same work on a fast and
    on a slow runner.
    """
    calibration = min(_thread_cpu(_calibration)[0] for _ in range(3))
    seconds, result = _thread_cpu(fn, *args, **kwargs)
    return seconds / calibration, result
