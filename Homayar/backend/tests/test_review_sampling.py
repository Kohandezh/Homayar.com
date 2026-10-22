"""HOM-72 (WP14-T01): the pure review sampling package ``review-sampling/1``.

``draw_sample`` picks review items by risk flag, by stratum and at random, from
a recorded seed.  The result depends only on the items, the normalized config
and the seed: never on the order of the items.  Every input rule refuses with a
``SamplingError`` code, and the three digests let a later draw prove that it
used the same config, population and selection.
"""

from __future__ import annotations

import hashlib
import json
import random

import pytest

from backend.review_sampling import (
    MAX_SEED,
    RISK_FLAGS,
    SAMPLER_VERSION,
    STRATUM_KEYS,
    UNASSIGNED,
    SampleItem,
    SamplingConfig,
    SamplingError,
    config_digest,
    draw_sample,
    population_digest,
)

DRAW_KEYS = [
    "sampler_version",
    "seed",
    "config",
    "config_digest",
    "population_count",
    "scoped_count",
    "population_digest",
    "risk_flagged_count",
    "selections",
    "selection_digest",
    "shortfalls",
]


def _rank(method: str, seed: int, item_id: str) -> str:
    return hashlib.sha256(f"{method}|{seed}|{item_id}".encode()).hexdigest()


def _canonical_digest(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _items(count: int) -> list[SampleItem]:
    return [SampleItem(f"m{index:03d}") for index in range(count)]


def _code(excinfo: pytest.ExceptionInfo[SamplingError]) -> str:
    return excinfo.value.code


# --- public names --------------------------------------------------------------


def test_public_constants():
    assert SAMPLER_VERSION == "review-sampling/1"
    assert STRATUM_KEYS == ("agent_user_id", "language", "source", "team_id")
    assert RISK_FLAGS == (
        "evidence_partial",
        "speaker_attribution_unknown",
        "transcript_corrected",
    )
    assert UNASSIGNED == "unassigned"
    assert MAX_SEED == 2**53 - 1
    assert issubclass(SamplingError, ValueError)


# --- items ---------------------------------------------------------------------


def test_item_defaults_and_normalization():
    item = SampleItem("m1")
    assert item.item_id == "m1"
    assert dict(item.strata) == dict.fromkeys(STRATUM_KEYS)
    assert item.risk_flags == ()

    item = SampleItem(
        "m2",
        strata={"team_id": "t1", "language": None},
        risk_flags=["transcript_corrected", "evidence_partial", "evidence_partial"],
    )
    assert item.strata["team_id"] == "t1"
    assert item.strata["language"] is None
    assert item.risk_flags == ("evidence_partial", "transcript_corrected")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"item_id": 7},
        {"item_id": None},
        {"item_id": "m1", "strata": {"region": "x"}},
        {"item_id": "m1", "strata": {"team_id": 5}},
        {"item_id": "m1", "strata": ["team_id"]},
        {"item_id": "m1", "risk_flags": ["needs_review"]},
        {"item_id": "m1", "risk_flags": "evidence_partial"},
        {"item_id": "m1", "risk_flags": [None]},
        {"item_id": "m1", "risk_flags": 3},
    ],
)
def test_invalid_items_are_refused(kwargs):
    with pytest.raises(SamplingError) as excinfo:
        SampleItem(**kwargs)
    assert _code(excinfo) == "item_invalid"


def test_non_item_entries_and_duplicates_are_refused():
    config = {"random_size": 1}
    with pytest.raises(SamplingError) as excinfo:
        draw_sample([{"item_id": "m1"}], config, seed=1)
    assert _code(excinfo) == "item_invalid"

    with pytest.raises(SamplingError) as excinfo:
        draw_sample([SampleItem("m1"), SampleItem("m1", strata={"team_id": "t"})], config, seed=1)
    assert _code(excinfo) == "duplicate_item"
    with pytest.raises(SamplingError) as excinfo:
        population_digest([SampleItem("m1"), SampleItem("m1")])
    assert _code(excinfo) == "duplicate_item"


# --- config --------------------------------------------------------------------


def test_config_defaults_and_normalized_form():
    config = SamplingConfig(random_size=3)
    assert config.normalized() == {
        "random_size": 3,
        "stratify_by": [],
        "per_stratum_size": 0,
        "risk_flags": [],
        "risk_cap": 100,
        "scope": {},
    }
    config = SamplingConfig(
        stratify_by=["team_id", "agent_user_id"],
        per_stratum_size=2,
        risk_flags=["transcript_corrected", "evidence_partial"],
        scope={"team_id": ["t2", "t1"], "language": ["fa"]},
    )
    normalized = config.normalized()
    assert normalized["stratify_by"] == ["agent_user_id", "team_id"]
    assert normalized["risk_flags"] == ["evidence_partial", "transcript_corrected"]
    assert list(normalized["scope"]) == ["language", "team_id"]
    assert normalized["scope"]["team_id"] == ["t1", "t2"]


@pytest.mark.parametrize(
    "raw",
    [
        {"random_size": 501},
        {"random_size": -1},
        {"random_size": True},
        {"random_size": "5"},
        {"random_size": 2.0},
        {"stratify_by": ["team_id"], "per_stratum_size": 0},
        {"stratify_by": [], "per_stratum_size": 1, "random_size": 1},
        {"stratify_by": ["team_id", "source", "language"], "per_stratum_size": 1},
        {"stratify_by": ["team_id", "team_id"], "per_stratum_size": 1},
        {"stratify_by": ["region"], "per_stratum_size": 1},
        {"stratify_by": ["team_id"], "per_stratum_size": 101},
        {"risk_flags": ["needs_review"]},
        {"risk_flags": ["evidence_partial", "evidence_partial"]},
        {"risk_flags": ["evidence_partial"], "risk_cap": 0},
        {"risk_flags": ["evidence_partial"], "risk_cap": 501},
        {"random_size": 1, "scope": {"region": ["x"]}},
        {"random_size": 1, "scope": {"team_id": []}},
        {"random_size": 1, "scope": {"team_id": ["a", "a"]}},
        {"random_size": 1, "scope": {"team_id": [f"t{i}" for i in range(21)]}},
        {"random_size": 1, "scope": {"team_id": [None]}},
        {"random_size": 1, "scope": {"team_id": "t1"}},
        {"random_size": 1, "unknown": 1},
    ],
)
def test_config_rule_breaks_are_refused(raw):
    with pytest.raises(SamplingError) as excinfo:
        SamplingConfig(**raw)
    assert _code(excinfo) == "sampling_config_invalid"
    with pytest.raises(SamplingError) as excinfo:
        draw_sample([SampleItem("m1")], raw, seed=1)
    assert _code(excinfo) == "sampling_config_invalid"


@pytest.mark.parametrize("raw", [None, [], "random_size=1", 5, {1: 2}])
def test_a_config_that_is_not_a_mapping_of_names_is_refused(raw):
    with pytest.raises(SamplingError) as excinfo:
        draw_sample([SampleItem("m1")], raw, seed=1)
    assert _code(excinfo) == "sampling_config_invalid"


@pytest.mark.parametrize(
    "raw", [{}, {"random_size": 0}, {"risk_cap": 5, "scope": {"source": ["x"]}}]
)
def test_a_config_that_selects_nothing_is_empty(raw):
    with pytest.raises(SamplingError) as excinfo:
        draw_sample([SampleItem("m1")], raw, seed=1)
    assert _code(excinfo) == "sampling_config_empty"


def test_config_boundaries_are_accepted():
    for raw in (
        {"random_size": 500},
        {"stratify_by": ["team_id", "source"], "per_stratum_size": 100},
        {"risk_flags": list(RISK_FLAGS), "risk_cap": 1},
        {"risk_flags": ["evidence_partial"], "risk_cap": 500},
        {"random_size": 1, "scope": {key: [f"v{i}" for i in range(20)] for key in STRATUM_KEYS}},
    ):
        draw = draw_sample([SampleItem("m1")], raw, seed=0)
        assert draw.config == SamplingConfig(**raw).normalized()


# --- seed ----------------------------------------------------------------------


@pytest.mark.parametrize("seed", [-1, MAX_SEED + 1, True, False, 1.0, "1", None])
def test_invalid_seeds_are_refused(seed):
    with pytest.raises(SamplingError) as excinfo:
        draw_sample([SampleItem("m1")], {"random_size": 1}, seed=seed)
    assert _code(excinfo) == "seed_invalid"


@pytest.mark.parametrize("seed", [0, 1, MAX_SEED])
def test_seed_boundaries_are_accepted(seed):
    draw = draw_sample([SampleItem("m1")], {"random_size": 1}, seed=seed)
    assert draw.seed == seed


# --- the algorithm -------------------------------------------------------------


def test_random_picks_the_lowest_ranks_and_reports_a_shortfall():
    items = _items(12)
    draw = draw_sample(items, {"random_size": 5}, seed=42)
    expected = sorted((item.item_id for item in items), key=lambda i: _rank("random", 42, i))[:5]
    assert [s["item_id"] for s in draw.selections] == expected
    assert all(s["reasons"] == ["random"] for s in draw.selections)
    assert all(s["stratum"] == {} and s["risk_flags"] == [] for s in draw.selections)
    assert draw.shortfalls == []

    short = draw_sample(items[:3], {"random_size": 5}, seed=42)
    assert len(short.selections) == 3
    assert short.shortfalls == [
        {"method": "random", "stratum": None, "requested": 5, "selected": 3}
    ]


def test_the_order_of_items_never_changes_the_result():
    items = [
        SampleItem(
            f"m{index}",
            strata={"team_id": f"t{index % 3}", "language": "fa" if index % 2 else None},
            risk_flags=["evidence_partial"] if index % 4 == 0 else [],
        )
        for index in range(40)
    ]
    config = {
        "random_size": 7,
        "stratify_by": ["team_id", "language"],
        "per_stratum_size": 2,
        "risk_flags": ["evidence_partial"],
        "risk_cap": 4,
    }
    reference = draw_sample(items, config, seed=7).to_json()
    shuffler = random.Random(3)  # noqa: S311 - test shuffling, not security
    for _ in range(5):
        shuffled = list(items)
        shuffler.shuffle(shuffled)
        assert draw_sample(shuffled, config, seed=7).to_json() == reference
        assert draw_sample(tuple(shuffled), config, seed=7).to_json() == reference
    assert (
        draw_sample(items, config, seed=8).to_json()["selection_digest"]
        != (reference["selection_digest"])
    )


def test_risk_picks_flagged_items_up_to_the_cap():
    items = [
        SampleItem("a", risk_flags=["evidence_partial"]),
        SampleItem("b", risk_flags=["transcript_corrected"]),
        SampleItem("c", risk_flags=["speaker_attribution_unknown", "evidence_partial"]),
        SampleItem("d"),
        SampleItem("e", risk_flags=["evidence_partial"]),
    ]
    draw = draw_sample(items, {"risk_flags": ["evidence_partial"], "risk_cap": 2}, seed=11)
    flagged = sorted(["a", "c", "e"], key=lambda i: _rank("risk", 11, i))
    assert [s["item_id"] for s in draw.selections] == flagged[:2]
    assert draw.risk_flagged_count == 3
    assert draw.shortfalls == []  # risk has no requested size
    by_id = {s["item_id"]: s for s in draw.selections}
    if "c" in by_id:
        assert by_id["c"]["risk_flags"] == ["evidence_partial", "speaker_attribution_unknown"]


def test_stratified_picks_per_stratum_and_reports_short_strata():
    items = [SampleItem(f"x{i}", strata={"team_id": "t1"}) for i in range(5)]
    items += [SampleItem("y0", strata={"team_id": "t2"})]
    items += [SampleItem(f"z{i}") for i in range(3)]  # no team: unassigned
    draw = draw_sample(items, {"stratify_by": ["team_id"], "per_stratum_size": 2}, seed=5)

    def ranked(ids):
        return sorted(ids, key=lambda i: _rank("stratified", 5, i))[:2]

    expected = ranked([f"x{i}" for i in range(5)]) + ["y0"] + ranked([f"z{i}" for i in range(3)])
    assert [s["item_id"] for s in draw.selections] == expected
    assert [s["stratum"] for s in draw.selections] == [
        {"team_id": "t1"},
        {"team_id": "t1"},
        {"team_id": "t2"},
        {"team_id": UNASSIGNED},
        {"team_id": UNASSIGNED},
    ]
    assert draw.shortfalls == [
        {"method": "stratified", "stratum": {"team_id": "t2"}, "requested": 2, "selected": 1}
    ]


def test_two_stratum_keys_sort_strata_by_their_value_tuples():
    items = [
        SampleItem("a", strata={"team_id": "t1", "agent_user_id": "u2"}),
        SampleItem("b", strata={"team_id": "t0", "agent_user_id": "u2"}),
        SampleItem("c", strata={"team_id": "t9", "agent_user_id": "u1"}),
    ]
    draw = draw_sample(
        items, {"stratify_by": ["team_id", "agent_user_id"], "per_stratum_size": 1}, seed=1
    )
    # strata (agent_user_id, team_id): (u1, t9) < (u2, t0) < (u2, t1)
    assert [s["item_id"] for s in draw.selections] == ["c", "b", "a"]
    assert draw.selections[0]["stratum"] == {"agent_user_id": "u1", "team_id": "t9"}


def test_an_item_picked_twice_keeps_its_first_position_and_gains_the_reason():
    items = [SampleItem(f"m{i}", risk_flags=["evidence_partial"]) for i in range(4)]
    draw = draw_sample(
        items,
        {
            "risk_flags": ["evidence_partial"],
            "risk_cap": 500,
            "stratify_by": ["source"],
            "per_stratum_size": 100,
            "random_size": 500,
        },
        seed=3,
    )
    assert [s["item_id"] for s in draw.selections] == sorted(
        [f"m{i}" for i in range(4)], key=lambda i: _rank("risk", 3, i)
    )
    assert all(s["reasons"] == ["risk", "stratified", "random"] for s in draw.selections)
    assert all(s["stratum"] == {"source": UNASSIGNED} for s in draw.selections)
    assert draw.shortfalls == [
        {
            "method": "stratified",
            "stratum": {"source": UNASSIGNED},
            "requested": 100,
            "selected": 4,
        },
        {"method": "random", "stratum": None, "requested": 500, "selected": 4},
    ]


def test_out_of_scope_items_are_never_selected():
    inside = SampleItem("in", strata={"team_id": "t1"}, risk_flags=["evidence_partial"])
    outside = SampleItem("out", strata={"team_id": "t2"}, risk_flags=["evidence_partial"])
    unassigned = SampleItem("none", risk_flags=["evidence_partial"])
    config = {
        "random_size": 10,
        "stratify_by": ["team_id"],
        "per_stratum_size": 10,
        "risk_flags": ["evidence_partial"],
        "scope": {"team_id": ["t1"]},
    }
    draw = draw_sample([inside, outside, unassigned], config, seed=1)
    assert [s["item_id"] for s in draw.selections] == ["in"]
    assert draw.population_count == 3
    assert draw.scoped_count == 1
    assert draw.risk_flagged_count == 1
    # a stratum with no in-scope item is not reported as short
    assert draw.shortfalls == [
        {"method": "stratified", "stratum": {"team_id": "t1"}, "requested": 10, "selected": 1},
        {"method": "random", "stratum": None, "requested": 10, "selected": 1},
    ]

    # the allow-control: the same item with an in-scope value is selectable
    widened = dict(config, scope={"team_id": ["t1", "t2", UNASSIGNED]})
    draw = draw_sample([inside, outside, unassigned], widened, seed=1)
    assert {s["item_id"] for s in draw.selections} == {"in", "out", "none"}


def test_scope_needs_every_key_to_match():
    items = [
        SampleItem("both", strata={"team_id": "t1", "language": "fa"}),
        SampleItem("team_only", strata={"team_id": "t1", "language": "en"}),
    ]
    draw = draw_sample(
        items, {"random_size": 5, "scope": {"team_id": ["t1"], "language": ["fa"]}}, seed=1
    )
    assert [s["item_id"] for s in draw.selections] == ["both"]


def test_an_empty_population_draws_nothing():
    draw = draw_sample([], {"random_size": 2, "risk_flags": ["evidence_partial"]}, seed=1)
    assert draw.selections == []
    assert draw.population_count == draw.scoped_count == draw.risk_flagged_count == 0
    assert draw.shortfalls == [{"method": "random", "stratum": None, "requested": 2, "selected": 0}]


# --- digests and JSON ----------------------------------------------------------


def test_to_json_has_the_contract_keys_in_order_and_round_trips():
    items = [
        SampleItem("b", strata={"team_id": "t"}),
        SampleItem("a", risk_flags=["evidence_partial"]),
    ]
    draw = draw_sample(items, {"random_size": 1}, seed=42)
    body = draw.to_json()
    assert list(body) == DRAW_KEYS
    assert body["sampler_version"] == SAMPLER_VERSION
    assert body["seed"] == 42
    assert json.loads(json.dumps(body)) == body
    body["selections"].clear()  # a copy: the draw itself is unchanged
    assert draw.to_json()["selections"]


def test_digests_follow_the_canonical_form():
    items = [
        SampleItem(
            "b", strata={"team_id": "t", "language": "فا"}, risk_flags=["transcript_corrected"]
        ),
        SampleItem("a", risk_flags=["evidence_partial", "speaker_attribution_unknown"]),
    ]
    raw = {"random_size": 2, "scope": {"team_id": ["t", UNASSIGNED]}}
    draw = draw_sample(items, raw, seed=9)

    assert draw.config_digest == config_digest(raw) == config_digest(SamplingConfig(**raw))
    assert draw.config_digest == _canonical_digest(SamplingConfig(**raw).normalized())
    expected_population = [
        {
            "item_id": "a",
            "strata": dict.fromkeys(STRATUM_KEYS),
            "risk_flags": ["evidence_partial", "speaker_attribution_unknown"],
        },
        {
            "item_id": "b",
            "strata": {"agent_user_id": None, "language": "فا", "source": None, "team_id": "t"},
            "risk_flags": ["transcript_corrected"],
        },
    ]
    assert (
        draw.population_digest == population_digest(items) == _canonical_digest(expected_population)
    )
    assert draw.selection_digest == _canonical_digest(
        [[s["item_id"], s["reasons"]] for s in draw.selections]
    )
    for value in (draw.config_digest, draw.population_digest, draw.selection_digest):
        assert len(value) == 64 and set(value) <= set("0123456789abcdef")


def test_the_population_digest_covers_out_of_scope_items_and_changes_with_them():
    base = [SampleItem("a", strata={"team_id": "t1"})]
    extra = base + [SampleItem("b", strata={"team_id": "t2"})]
    config = {"random_size": 1, "scope": {"team_id": ["t1"]}}
    first = draw_sample(base, config, seed=1)
    second = draw_sample(extra, config, seed=1)
    assert first.selection_digest == second.selection_digest
    assert first.population_digest != second.population_digest


def test_a_draw_is_reproduced_from_its_recorded_parts():
    items = [
        SampleItem(
            f"m{i}",
            strata={"source": "upload" if i % 2 else "api"},
            risk_flags=["evidence_partial"] if i % 3 == 0 else [],
        )
        for i in range(30)
    ]
    first = draw_sample(
        items,
        {
            "random_size": 4,
            "stratify_by": ["source"],
            "per_stratum_size": 3,
            "risk_flags": ["evidence_partial"],
            "risk_cap": 2,
        },
        seed=MAX_SEED,
    ).to_json()
    again = draw_sample(list(reversed(items)), first["config"], seed=first["seed"]).to_json()
    assert again == first


# --- turn 2: every validation path, read-only strata ------------------------------


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ({"random_size": 501}, "sampling_config_invalid"),
        ({"random_size": True}, "sampling_config_invalid"),
        ({"random_size": 1, "unknown": 1}, "sampling_config_invalid"),
        ({"stratify_by": ["team_id"], "per_stratum_size": 0}, "sampling_config_invalid"),
        ("random_size", "sampling_config_invalid"),
        ({}, "sampling_config_empty"),
        ({"random_size": 0, "risk_cap": 5}, "sampling_config_empty"),
    ],
)
def test_model_validate_raises_sampling_errors_too(raw, code):
    with pytest.raises(SamplingError) as excinfo:
        SamplingConfig.model_validate(raw)
    assert _code(excinfo) == code
    with pytest.raises(SamplingError) as excinfo:
        SamplingConfig.model_validate_json(json.dumps(raw))
    assert _code(excinfo) == code
    if isinstance(raw, dict):
        with pytest.raises(SamplingError) as excinfo:
            SamplingConfig(**raw)
        assert _code(excinfo) == code


def test_model_validate_accepts_and_normalizes_a_valid_config():
    raw = {"stratify_by": ["team_id", "source"], "per_stratum_size": 2, "random_size": 1}
    by_validate = SamplingConfig.model_validate(raw)
    by_json = SamplingConfig.model_validate_json(json.dumps(raw))
    assert by_validate.normalized() == by_json.normalized() == SamplingConfig(**raw).normalized()
    assert by_validate.stratify_by == ["source", "team_id"]


def test_item_strata_are_read_only():
    item = SampleItem("m1", strata={"team_id": "t1"})
    with pytest.raises(TypeError):
        item.strata["team_id"] = 5  # type: ignore[index]
    with pytest.raises(TypeError):
        del item.strata["team_id"]  # type: ignore[attr-defined]
    assert item.strata["team_id"] == "t1"
    # the caller's own dict stays separate
    given = {"team_id": "t1"}
    item = SampleItem("m2", strata=given)
    given["team_id"] = "t2"
    assert item.strata["team_id"] == "t1"
    draw = draw_sample([item], {"random_size": 1}, seed=1)
    assert draw.to_json()["selections"][0]["item_id"] == "m2"
    assert draw.population_digest == _canonical_digest(
        [
            {
                "item_id": "m2",
                "strata": {**dict.fromkeys(STRATUM_KEYS), "team_id": "t1"},
                "risk_flags": [],
            }
        ]
    )
