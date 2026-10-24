"""Scorecard state and missing-data indicators of the aggregates (HOM-81, WP15-T03).

``GET /trends/issues`` and ``GET /admin/coverage`` both carry a ``scorecard`` block and a
``missing_data`` summary.  Scorecards (HOM-54) do not exist yet, so the block is always
``not_available`` with ``scorecard_results_unavailable``, never a made-up version.

Every indicator is a pure function of other fields of the same response: no query and no
new number.  ``indicators`` is sorted and holds each code at most once.

Counter rules, for each coverage counter named in the response:

    <name>_incomplete   status computed and numerator < denominator
    <name>_unavailable  status not_computable for a reason other than empty_denominator

Response rules: ``scorecard_unavailable`` (both), ``empty_population`` (trends: nothing
included; coverage: nothing ingested), and for the trend rows of the whole window (not the
points) ``missing_values``, ``pending_review``, ``insufficient_sample`` and
``source_unavailable``.

HOM-299 (ADR 0041): ``GET /voicemails/analytics`` carries a population ``coverage`` block
(``population_coverage_block``) and ``population_missing_data``: the scorecard and counter
rules, and ``empty_population`` when the processing denominator is 0.  The items of
``GET /organizations/{id}/aggregates`` carry the HOM-82 block's metadata unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from backend.issue_trends import CellReason, CellStatus
from backend.services.coverage_service import (
    COVERAGE_VERSION,
    REASON_EMPTY_DENOMINATOR,
    STATUS_COMPUTED,
    STATUS_NOT_COMPUTABLE,
    CoverageWindow,
)

SCORECARD_NOT_AVAILABLE = "not_available"

TREND_COUNTERS = ("processing", "evaluation")

# HOM-299: the population of a coverage block that is not the ledger, and the column its
# window filters.
BASIS_VOICEMAIL_POPULATION = "voicemail_population"
WINDOW_BASIS_VOICEMAIL_RECEIVED_AT = "voicemail_received_at"
COVERAGE_COUNTERS = ("ingestion", "processing", "evaluation", "review")

MISSING_DATA_INDICATORS = frozenset(
    {
        *(
            f"{name}_{state}"
            for name in COVERAGE_COUNTERS
            for state in ("incomplete", "unavailable")
        ),
        "scorecard_unavailable",
        "empty_population",
        "missing_values",
        "pending_review",
        "insufficient_sample",
        "source_unavailable",
    }
)


def scorecard_block() -> dict[str, Any]:
    """The scorecard state of every aggregate until HOM-54 adds ``available``.  A new
    dict each call, so a caller cannot change the next answer."""
    return {
        "status": SCORECARD_NOT_AVAILABLE,
        "version": None,
        "digest": None,
        "reason": CellReason.SCORECARD_RESULTS_UNAVAILABLE.value,
    }


def counter_indicators(name: str, counter: Mapping[str, Any]) -> set[str]:
    """``<name>_incomplete`` or ``<name>_unavailable`` of one coverage counter."""
    if counter["status"] == STATUS_COMPUTED and counter["numerator"] < counter["denominator"]:
        return {f"{name}_incomplete"}
    if counter["status"] == STATUS_NOT_COMPUTABLE and counter["reason"] != REASON_EMPTY_DENOMINATOR:
        return {f"{name}_unavailable"}
    return set()


def _scorecard_indicators(scorecard: Mapping[str, Any]) -> set[str]:
    return {"scorecard_unavailable"} if scorecard["status"] == SCORECARD_NOT_AVAILABLE else set()


def _row_indicators(rows: Iterable[Mapping[str, Any]]) -> set[str]:
    codes: set[str] = set()
    for row in rows:
        cell = row["cell"]
        if sum(row["missing"].values()) > 0:
            codes.add("missing_values")
        if (row["pending_review"] or 0) > 0 or (row["absent_pending_review"] or 0) > 0:
            codes.add("pending_review")
        if cell["status"] == CellStatus.INSUFFICIENT_SAMPLE.value:
            codes.add("insufficient_sample")
        if (
            cell["status"] == CellStatus.NOT_COMPUTABLE.value
            and cell["reason"] != CellReason.EMPTY_DENOMINATOR.value
        ):
            codes.add("source_unavailable")
    return codes


def _missing_data(codes: set[str]) -> dict[str, Any]:
    return {"indicators": sorted(codes)}


def trend_missing_data(answer: Mapping[str, Any]) -> dict[str, Any]:
    """``missing_data`` of a trend answer that already has ``coverage`` and ``scorecard``."""
    codes = _scorecard_indicators(answer["scorecard"])
    for name in TREND_COUNTERS:
        codes |= counter_indicators(name, answer["coverage"][name])
    if answer["population"]["included"] == 0:
        codes.add("empty_population")
    codes |= _row_indicators(answer["rows"])
    return _missing_data(codes)


def coverage_missing_data(
    counters: Mapping[str, Mapping[str, Any]], scorecard: Mapping[str, Any]
) -> dict[str, Any]:
    """``missing_data`` of a ``GET /admin/coverage`` answer."""
    codes = _scorecard_indicators(scorecard)
    for name in COVERAGE_COUNTERS:
        codes |= counter_indicators(name, counters[name])
    if counters["ingestion"]["denominator"] == 0:
        codes.add("empty_population")
    return _missing_data(codes)


def population_coverage_block(
    window: CoverageWindow, *, window_basis: str, basis: str, counters: Mapping[str, Any]
) -> dict[str, Any]:
    """The ``coverage`` key over a population that is not the ledger (HOM-299): the
    ``coverage/1`` processing and evaluation counters."""
    return {
        "version": COVERAGE_VERSION,
        "window": {"from": window.start, "to": window.end, "basis": window_basis},
        "basis": basis,
        "processing": counters["processing"],
        "evaluation": counters["evaluation"],
    }


def population_missing_data(
    coverage: Mapping[str, Any], scorecard: Mapping[str, Any]
) -> dict[str, Any]:
    """``missing_data`` of a population coverage block (HOM-299)."""
    codes = _scorecard_indicators(scorecard)
    for name in TREND_COUNTERS:
        codes |= counter_indicators(name, coverage[name])
    if coverage["processing"]["denominator"] == 0:
        codes.add("empty_population")
    return _missing_data(codes)
