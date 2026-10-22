"""The ``review-sampling/1`` draw: scope, rank, the three methods, digests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from backend.review_sampling.models import (
    STRATUM_KEYS,
    SampleDraw,
    SampleItem,
    SamplingConfig,
    SamplingError,
    StratumKey,
    coerce_config,
    validate_seed,
)


def _canonical_digest(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _rank(method: str, seed: int, item_id: str) -> str:
    return hashlib.sha256(f"{method}|{seed}|{item_id}".encode()).hexdigest()


def _checked_items(items: Iterable[SampleItem]) -> list[SampleItem]:
    checked: list[SampleItem] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, SampleItem):
            raise SamplingError("item_invalid", "every item must be a SampleItem")
        if item.item_id in seen:
            raise SamplingError("duplicate_item", f"item {item.item_id!r} is given twice")
        seen.add(item.item_id)
        checked.append(item)
    return checked


def config_digest(config: SamplingConfig | Mapping[str, Any]) -> str:
    """sha256 of the canonical JSON of the normalized config."""
    return _canonical_digest(coerce_config(config).normalized())


def population_digest(items: Iterable[SampleItem]) -> str:
    """sha256 of every given item (in and out of scope), sorted by ``item_id``."""
    entries = [
        {
            "item_id": item.item_id,
            "strata": {key: item.strata.get(key) for key in STRATUM_KEYS},
            "risk_flags": list(item.risk_flags),
        }
        for item in sorted(_checked_items(items), key=lambda item: item.item_id)
    ]
    return _canonical_digest(entries)


def _in_scope(item: SampleItem, scope: Mapping[StratumKey, list[str]]) -> bool:
    return all(item.stratum_value(key) in values for key, values in scope.items())


def draw_sample(
    items: Iterable[SampleItem], config: SamplingConfig | Mapping[str, Any], *, seed: int
) -> SampleDraw:
    """Draw review items from ``items``.  Pure and deterministic.

    Checks run in this order: config, seed, items.  See ``README.md`` for the
    algorithm; it is part of ``SAMPLER_VERSION``.
    """
    settings = coerce_config(config)
    seed = validate_seed(seed)
    population = _checked_items(items)
    scoped = [item for item in population if _in_scope(item, settings.scope)]

    def ranked(method: str, pool: list[SampleItem]) -> list[SampleItem]:
        return sorted(pool, key=lambda item: _rank(method, seed, item.item_id))

    picks: list[tuple[str, SampleItem]] = []
    shortfalls: list[dict[str, Any]] = []

    wanted_flags = set(settings.risk_flags)
    flagged = [item for item in scoped if wanted_flags.intersection(item.risk_flags)]
    picks.extend(("risk", item) for item in ranked("risk", flagged)[: settings.risk_cap])

    keys = settings.stratify_by
    if keys:
        strata: dict[tuple[str, ...], list[SampleItem]] = {}
        for item in scoped:
            strata.setdefault(tuple(item.stratum_value(key) for key in keys), []).append(item)
        for values in sorted(strata):
            members = strata[values]
            picks.extend(
                ("stratified", item)
                for item in ranked("stratified", members)[: settings.per_stratum_size]
            )
            if len(members) < settings.per_stratum_size:
                shortfalls.append(
                    {
                        "method": "stratified",
                        "stratum": dict(zip(keys, values, strict=True)),
                        "requested": settings.per_stratum_size,
                        "selected": len(members),
                    }
                )

    if settings.random_size:
        picks.extend(("random", item) for item in ranked("random", scoped)[: settings.random_size])
        if len(scoped) < settings.random_size:
            shortfalls.append(
                {
                    "method": "random",
                    "stratum": None,
                    "requested": settings.random_size,
                    "selected": len(scoped),
                }
            )

    selected: dict[str, dict[str, Any]] = {}
    for method, item in picks:
        entry = selected.get(item.item_id)
        if entry is None:
            selected[item.item_id] = {
                "item_id": item.item_id,
                "reasons": [method],
                "stratum": {key: item.stratum_value(key) for key in keys},
                "risk_flags": list(item.risk_flags),
            }
        elif method not in entry["reasons"]:
            entry["reasons"].append(method)
    selections = list(selected.values())

    normalized = settings.normalized()
    return SampleDraw(
        seed=seed,
        config=normalized,
        config_digest=_canonical_digest(normalized),
        population_count=len(population),
        scoped_count=len(scoped),
        population_digest=population_digest(population),
        risk_flagged_count=len(flagged),
        selections=selections,
        selection_digest=_canonical_digest(
            [[entry["item_id"], entry["reasons"]] for entry in selections]
        ),
        shortfalls=shortfalls,
    )
