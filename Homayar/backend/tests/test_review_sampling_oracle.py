from __future__ import annotations

import ast
import hashlib
import importlib
import json
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
PACKAGE_DIR = BACKEND / "review_sampling"

VERSION = "review-sampling/1"
KEYS = ("agent_user_id", "language", "source", "team_id")
FLAGS = ("evidence_partial", "speaker_attribution_unknown", "transcript_corrected")
UNASSIGNED = "unassigned"
MAX_SEED = 2**53 - 1
CONFIG_FIELDS = {
    "random_size",
    "stratify_by",
    "per_stratum_size",
    "risk_flags",
    "risk_cap",
    "scope",
}
DRAW_KEYS = {
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
}


def _rs():
    return importlib.import_module("backend.review_sampling")


def _sha(value) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _rank(method: str, seed: int, item_id: str) -> str:
    return hashlib.sha256(f"{method}|{seed}|{item_id}".encode()).hexdigest()


def _normalized(config: dict) -> dict:
    scope = config.get("scope", {})
    return {
        "random_size": config.get("random_size", 0),
        "stratify_by": sorted(config.get("stratify_by", [])),
        "per_stratum_size": config.get("per_stratum_size", 0),
        "risk_flags": sorted(config.get("risk_flags", [])),
        "risk_cap": config.get("risk_cap", 100),
        "scope": {key: sorted(scope[key]) for key in sorted(scope)},
    }


def _value(spec: tuple, key: str) -> str:
    value = spec[1].get(key)
    return UNASSIGNED if value is None else value


def _population_entries(specs: list[tuple]) -> list[dict]:
    return [
        {
            "item_id": item_id,
            "strata": {key: strata.get(key) for key in KEYS},
            "risk_flags": sorted(flags),
        }
        for item_id, strata, flags in sorted(specs, key=lambda spec: spec[0])
    ]


def _reference(specs: list[tuple], config: dict, seed: int) -> dict:
    norm = _normalized(config)
    scoped = [
        spec
        for spec in specs
        if all(_value(spec, key) in values for key, values in norm["scope"].items())
    ]
    wanted = set(norm["risk_flags"])
    flagged = [spec for spec in scoped if wanted & set(spec[2])]

    def by_rank(method: str, group: list[tuple]) -> list[tuple]:
        return sorted(group, key=lambda spec: _rank(method, seed, spec[0]))

    stratify = norm["stratify_by"]

    def stratum_of(spec: tuple) -> tuple:
        return tuple(_value(spec, key) for key in stratify)

    risk_picks = by_rank("risk", flagged)[: norm["risk_cap"]]
    strata: dict[tuple, list[tuple]] = {}
    if stratify:
        for spec in scoped:
            strata.setdefault(stratum_of(spec), []).append(spec)
    stratified_picks = []
    shortfalls = []
    for key in sorted(strata):
        members = strata[key]
        stratified_picks.extend(by_rank("stratified", members)[: norm["per_stratum_size"]])
        if len(members) < norm["per_stratum_size"]:
            shortfalls.append(
                {
                    "method": "stratified",
                    "stratum": dict(zip(stratify, key, strict=True)),
                    "requested": norm["per_stratum_size"],
                    "selected": len(members),
                }
            )
    random_picks = by_rank("random", scoped)[: norm["random_size"]]
    if len(scoped) < norm["random_size"]:
        shortfalls.append(
            {
                "method": "random",
                "stratum": None,
                "requested": norm["random_size"],
                "selected": len(scoped),
            }
        )

    order: list[str] = []
    reasons: dict[str, list[str]] = {}
    by_id = {spec[0]: spec for spec in specs}
    for method, picks in (
        ("risk", risk_picks),
        ("stratified", stratified_picks),
        ("random", random_picks),
    ):
        for spec in picks:
            if spec[0] not in reasons:
                order.append(spec[0])
                reasons[spec[0]] = []
            reasons[spec[0]].append(method)
    selections = [
        {
            "item_id": item_id,
            "reasons": reasons[item_id],
            "stratum": {key: _value(by_id[item_id], key) for key in stratify},
            "risk_flags": sorted(by_id[item_id][2]),
        }
        for item_id in order
    ]
    return {
        "sampler_version": VERSION,
        "seed": seed,
        "config": norm,
        "config_digest": _sha(norm),
        "population_count": len(specs),
        "scoped_count": len(scoped),
        "population_digest": _sha(_population_entries(specs)),
        "risk_flagged_count": len(flagged),
        "selections": selections,
        "selection_digest": _sha([[s["item_id"], s["reasons"]] for s in selections]),
        "shortfalls": shortfalls,
    }


def _items(specs: list[tuple]):
    rs = _rs()
    return [rs.SampleItem(item_id, strata, flags) for item_id, strata, flags in specs]


def _draw(specs: list[tuple], config, seed: int) -> dict:
    return _rs().draw_sample(_items(specs), config, seed=seed).to_json()


def _sorted_shortfalls(entries: list[dict]) -> list[dict]:
    return sorted(entries, key=lambda entry: json.dumps(entry, sort_keys=True))


def _assert_matches(specs: list[tuple], config: dict, seed: int) -> dict:
    actual = _draw(specs, config, seed)
    expected = _reference(specs, config, seed)
    assert set(actual) == DRAW_KEYS
    assert _sorted_shortfalls(actual.pop("shortfalls")) == _sorted_shortfalls(
        expected.pop("shortfalls")
    )
    assert actual == expected
    return expected


def _population(count: int = 60) -> list[tuple]:
    teams = ["team-a", "team-b", None, "team-c"]
    agents = ["agent-1", "agent-2", None]
    languages = ["fa", "en"]
    sources = ["upload", "api", None]
    specs = []
    for n in range(count):
        strata = {
            "team_id": teams[n % 4],
            "agent_user_id": agents[n % 3],
            "language": languages[(n // 3) % 2],
            "source": sources[(n // 5) % 3],
        }
        flags = []
        if n % 7 == 0:
            flags.append("evidence_partial")
        if n % 11 == 0:
            flags.append("transcript_corrected")
        if n % 13 == 5:
            flags.append("speaker_attribution_unknown")
        specs.append((f"m-{n:03d}", strata, tuple(reversed(flags))))
    return specs


def _error(callable_, *args, **kwargs) -> str:
    rs = _rs()
    with pytest.raises(rs.SamplingError) as caught:
        callable_(*args, **kwargs)
    assert isinstance(caught.value, ValueError)
    return caught.value.code


def _config_code(config) -> str:
    rs = _rs()
    return _error(lambda: rs.draw_sample(_items(_population(5)), config, seed=1))


def test_public_names_and_constants():
    rs = _rs()
    assert rs.SAMPLER_VERSION == VERSION
    assert tuple(rs.STRATUM_KEYS) == KEYS
    assert tuple(rs.RISK_FLAGS) == FLAGS
    assert rs.UNASSIGNED == UNASSIGNED
    assert rs.MAX_SEED == MAX_SEED == 9007199254740991
    assert issubclass(rs.SamplingError, ValueError)
    for name in ("SampleItem", "SamplingConfig", "draw_sample", "config_digest"):
        assert callable(getattr(rs, name)), name
    assert callable(rs.population_digest)


@pytest.mark.parametrize("seed", [0, 1, 42, 123456789, MAX_SEED])
def test_random_draw_matches_the_published_rank(seed):
    expected = _assert_matches(_population(), {"random_size": 10}, seed)
    ids = [s["item_id"] for s in expected["selections"]]
    assert len(ids) == len(set(ids)) == 10
    assert all(s["reasons"] == ["random"] and s["stratum"] == {} for s in expected["selections"])


def test_random_rank_is_pinned_to_known_hashes():
    specs = [("a", {}, ()), ("b", {}, ()), ("c", {}, ()), ("d", {}, ())]
    ranks = {item_id: _rank("random", 7, item_id) for item_id, _, _ in specs}
    assert ranks["a"] == hashlib.sha256(b"random|7|a").hexdigest()
    expected = sorted(ranks, key=ranks.get)[:2]
    draw = _draw(specs, {"random_size": 2}, 7)
    assert [s["item_id"] for s in draw["selections"]] == expected


def test_stratified_draw_by_one_key():
    expected = _assert_matches(
        _population(), {"stratify_by": ["team_id"], "per_stratum_size": 3}, 5
    )
    strata = [s["stratum"]["team_id"] for s in expected["selections"]]
    assert strata == sorted(strata)
    assert {key: strata.count(key) for key in set(strata)} == {
        "team-a": 3,
        "team-b": 3,
        "team-c": 3,
        UNASSIGNED: 3,
    }


def test_stratified_draw_by_two_keys_given_in_any_order():
    config = {"stratify_by": ["team_id", "language"], "per_stratum_size": 2}
    expected = _assert_matches(_population(), config, 99)
    reordered = _draw(_population(), {**config, "stratify_by": ["language", "team_id"]}, 99)
    assert reordered["selections"] == expected["selections"]
    assert reordered["config"]["stratify_by"] == ["language", "team_id"]
    assert all(set(s["stratum"]) == {"language", "team_id"} for s in expected["selections"])


def test_risk_draw_respects_the_cap_and_counts_every_flagged_item():
    config = {"risk_flags": ["transcript_corrected", "evidence_partial"], "risk_cap": 4}
    expected = _assert_matches(_population(), config, 3)
    assert expected["risk_flagged_count"] > 4
    assert len(expected["selections"]) == 4
    for selection in expected["selections"]:
        assert selection["reasons"] == ["risk"]
        assert {"transcript_corrected", "evidence_partial"} & set(selection["risk_flags"])


def test_risk_selection_reports_every_flag_of_the_item_sorted():
    specs = [("x", {}, ("transcript_corrected", "speaker_attribution_unknown")), ("y", {}, ())]
    draw = _draw(specs, {"risk_flags": ["transcript_corrected"]}, 1)
    assert draw["selections"] == [
        {
            "item_id": "x",
            "reasons": ["risk"],
            "stratum": {},
            "risk_flags": ["speaker_attribution_unknown", "transcript_corrected"],
        }
    ]
    assert draw["risk_flagged_count"] == 1


def test_unflagged_item_is_not_a_risk_pick_but_a_flagged_one_is():
    specs = [("plain", {}, ()), ("flagged", {}, ("evidence_partial",))]
    draw = _draw(specs, {"risk_flags": ["evidence_partial"]}, 11)
    assert [s["item_id"] for s in draw["selections"]] == ["flagged"]
    other_flag = _draw(specs, {"risk_flags": ["transcript_corrected"]}, 11)
    assert other_flag["selections"] == [] and other_flag["risk_flagged_count"] == 0


@pytest.mark.parametrize("seed", [0, 17, 2**40])
def test_all_three_methods_merge_reasons_in_fixed_order(seed):
    config = {
        "random_size": 15,
        "stratify_by": ["language"],
        "per_stratum_size": 6,
        "risk_flags": list(FLAGS),
        "risk_cap": 8,
    }
    expected = _assert_matches(_population(), config, seed)
    for selection in expected["selections"]:
        assert selection["reasons"] == [
            m for m in ("risk", "stratified", "random") if m in selection["reasons"]
        ]
        assert set(selection["stratum"]) == {"language"}


def test_an_item_picked_twice_keeps_its_first_place():
    specs = [("only", {"language": "fa"}, ("evidence_partial",))]
    config = {
        "random_size": 1,
        "stratify_by": ["language"],
        "per_stratum_size": 1,
        "risk_flags": ["evidence_partial"],
    }
    draw = _draw(specs, config, 0)
    assert draw["selections"] == [
        {
            "item_id": "only",
            "reasons": ["risk", "stratified", "random"],
            "stratum": {"language": "fa"},
            "risk_flags": ["evidence_partial"],
        }
    ]
    assert draw["selection_digest"] == _sha([["only", ["risk", "stratified", "random"]]])


def test_item_order_never_changes_the_draw():
    specs = _population()
    config = {
        "random_size": 9,
        "stratify_by": ["source", "team_id"],
        "per_stratum_size": 1,
        "risk_flags": ["evidence_partial"],
        "risk_cap": 3,
    }
    baseline = _draw(specs, config, 2024)
    shuffled = sorted(specs, key=lambda spec: hashlib.sha256(spec[0].encode()).hexdigest())
    for variant in (list(reversed(specs)), shuffled):
        assert _draw(variant, config, 2024) == baseline


def test_same_seed_repeats_and_another_seed_differs():
    config = {"random_size": 10}
    first = _draw(_population(), config, 77)
    assert _draw(_population(), config, 77) == first
    other = _draw(_population(), config, 78)
    assert other["selection_digest"] != first["selection_digest"]
    assert other["population_digest"] == first["population_digest"]
    assert other["config_digest"] == first["config_digest"]


def test_item_outside_scope_is_never_selected_and_inside_is():
    outside = ("out", {"team_id": "team-z"}, ("evidence_partial",))
    inside = ("out", {"team_id": "team-a"}, ("evidence_partial",))
    config = {
        "random_size": 5,
        "stratify_by": ["team_id"],
        "per_stratum_size": 5,
        "risk_flags": ["evidence_partial"],
        "scope": {"team_id": ["team-a", "team-b"]},
    }
    base = [("keep", {"team_id": "team-b"}, ())]
    denied = _assert_matches(base + [outside], config, 4)
    assert "out" not in {s["item_id"] for s in denied["selections"]}
    assert denied["population_count"] == 2 and denied["scoped_count"] == 1
    assert denied["risk_flagged_count"] == 0
    allowed = _assert_matches(base + [inside], config, 4)
    assert "out" in {s["item_id"] for s in allowed["selections"]}
    assert allowed["scoped_count"] == 2 and allowed["risk_flagged_count"] == 1


def test_scope_needs_every_key_and_matches_unassigned_for_missing_values():
    specs = [
        ("a", {"team_id": "team-a", "language": "fa"}, ()),
        ("b", {"team_id": "team-a", "language": "en"}, ()),
        ("c", {"language": "fa"}, ()),
        ("d", {"team_id": None, "language": "fa"}, ()),
        ("e", {"team_id": "team-b", "language": "fa"}, ()),
    ]
    config = {"random_size": 10, "scope": {"team_id": ["team-a", UNASSIGNED], "language": ["fa"]}}
    expected = _assert_matches(specs, config, 8)
    assert {s["item_id"] for s in expected["selections"]} == {"a", "c", "d"}
    assert expected["scoped_count"] == 3


def test_random_shortfall_when_scope_is_smaller_than_the_size():
    specs = _population(12)
    expected = _assert_matches(specs, {"random_size": 20}, 1)
    assert len(expected["selections"]) == 12
    assert _draw(specs, {"random_size": 20}, 1)["shortfalls"] == [
        {"method": "random", "stratum": None, "requested": 20, "selected": 12}
    ]
    exact = _draw(specs, {"random_size": 12}, 1)
    assert exact["shortfalls"] == []


def test_stratified_shortfall_per_small_stratum_and_none_for_absent_strata():
    specs = [
        ("a1", {"team_id": "team-a"}, ()),
        ("a2", {"team_id": "team-a"}, ()),
        ("a3", {"team_id": "team-a"}, ()),
        ("b1", {"team_id": "team-b"}, ()),
        ("z1", {"team_id": "team-z"}, ()),
    ]
    config = {
        "stratify_by": ["team_id"],
        "per_stratum_size": 2,
        "scope": {"team_id": ["team-a", "team-b", "team-c"]},
    }
    draw = _draw(specs, config, 6)
    assert draw["shortfalls"] == [
        {"method": "stratified", "stratum": {"team_id": "team-b"}, "requested": 2, "selected": 1}
    ]
    assert [s["stratum"] for s in draw["selections"]] == [
        {"team_id": "team-a"},
        {"team_id": "team-a"},
        {"team_id": "team-b"},
    ]
    _assert_matches(specs, config, 6)


def test_to_json_holds_the_normalized_config_and_hex_digests():
    config = {
        "risk_flags": ["transcript_corrected", "evidence_partial"],
        "stratify_by": ["team_id", "agent_user_id"],
        "per_stratum_size": 1,
        "scope": {"team_id": ["team-b", "team-a"], "language": ["fa"]},
    }
    draw = _draw(_population(), config, 42)
    assert draw["config"] == {
        "random_size": 0,
        "stratify_by": ["agent_user_id", "team_id"],
        "per_stratum_size": 1,
        "risk_flags": ["evidence_partial", "transcript_corrected"],
        "risk_cap": 100,
        "scope": {"language": ["fa"], "team_id": ["team-a", "team-b"]},
    }
    assert draw["sampler_version"] == VERSION and draw["seed"] == 42
    for key in ("config_digest", "population_digest", "selection_digest"):
        assert len(draw[key]) == 64 and int(draw[key], 16) >= 0, key
    assert json.loads(json.dumps(draw)) == draw


def test_config_digest_is_over_the_normalized_config():
    rs = _rs()
    config = {"random_size": 3, "risk_flags": ["transcript_corrected", "evidence_partial"]}
    reordered = {"risk_flags": ["evidence_partial", "transcript_corrected"], "random_size": 3}
    expected = _sha(_normalized(config))
    assert _draw(_population(), config, 1)["config_digest"] == expected
    assert _draw(_population(), reordered, 1)["config_digest"] == expected
    assert rs.config_digest(rs.SamplingConfig.model_validate(config)) == expected
    assert _draw(_population(), {**config, "risk_cap": 99}, 1)["config_digest"] != expected


def test_population_digest_covers_every_item_and_ignores_scope_and_order():
    rs = _rs()
    specs = _population(20)
    expected = _sha(_population_entries(specs))
    assert rs.population_digest(_items(specs)) == expected
    assert rs.population_digest(_items(list(reversed(specs)))) == expected
    scoped = _draw(specs, {"random_size": 1, "scope": {"team_id": ["team-a"]}}, 1)
    assert scoped["population_digest"] == expected
    assert scoped["population_count"] == 20


def test_population_digest_writes_null_for_a_missing_or_none_stratum():
    rs = _rs()
    missing = [("m", {"team_id": "t"}, ())]
    explicit_none = [("m", {"team_id": "t", "language": None}, ())]
    expected = _sha(
        [
            {
                "item_id": "m",
                "strata": {
                    "agent_user_id": None,
                    "language": None,
                    "source": None,
                    "team_id": "t",
                },
                "risk_flags": [],
            }
        ]
    )
    assert rs.population_digest(_items(missing)) == expected
    assert rs.population_digest(_items(explicit_none)) == expected
    assert rs.population_digest(_items([("m", {"team_id": "u"}, ())])) != expected
    flagged = [("m", {"team_id": "t"}, ("transcript_corrected", "evidence_partial"))]
    assert rs.population_digest(_items(flagged)) == _sha(_population_entries(flagged))


def test_persian_and_unicode_values_hash_as_utf8_without_escapes():
    specs = [("جلسه-۱", {"team_id": "تیم‌الف", "language": "fa"}, ())]
    _assert_matches(specs, {"random_size": 1, "scope": {"team_id": ["تیم‌الف"]}}, 9)


def test_draw_accepts_a_config_model_or_a_plain_dict():
    rs = _rs()
    config = {"random_size": 4, "risk_flags": ["evidence_partial"]}
    as_dict = _draw(_population(), config, 10)
    as_model = _draw(_population(), rs.SamplingConfig.model_validate(config), 10)
    assert as_model == as_dict


def test_config_model_is_frozen_and_refuses_unknown_keys():
    rs = _rs()
    config = rs.SamplingConfig.model_validate({"random_size": 1})
    with pytest.raises((TypeError, ValueError)):
        config.random_size = 2
    with pytest.raises(ValueError):
        rs.SamplingConfig.model_validate({"random_size": 1, "sample_size": 1})


@pytest.mark.parametrize(
    "config",
    [
        {"random_size": -1},
        {"random_size": 501},
        {"random_size": 1, "sample_size": 3},
        {"stratify_by": ["team_id"], "per_stratum_size": 0},
        {"stratify_by": ["team_id"]},
        {"random_size": 1, "per_stratum_size": 2},
        {"stratify_by": ["team_id"], "per_stratum_size": 101},
        {"stratify_by": ["team_id", "language", "source"], "per_stratum_size": 1},
        {"stratify_by": ["team_id", "team_id"], "per_stratum_size": 1},
        {"stratify_by": ["region"], "per_stratum_size": 1},
        {"risk_flags": ["needs_review"]},
        {"risk_flags": ["evidence_partial", "evidence_partial"]},
        {"risk_flags": ["evidence_partial"], "risk_cap": 0},
        {"risk_flags": ["evidence_partial"], "risk_cap": 501},
        {"random_size": 1, "scope": {"region": ["north"]}},
        {"random_size": 1, "scope": {"team_id": []}},
        {"random_size": 1, "scope": {"team_id": ["a", "a"]}},
        {"random_size": 1, "scope": {"team_id": [f"t{n}" for n in range(21)]}},
        {"random_size": 1, "scope": {"team_id": [7]}},
    ],
)
def test_config_rule_breaks_are_refused(config):
    assert _config_code(config) == "sampling_config_invalid"


@pytest.mark.parametrize(
    "config",
    [
        {"random_size": 500},
        {"random_size": 0, "risk_flags": ["evidence_partial"]},
        {"stratify_by": ["team_id"], "per_stratum_size": 100},
        {"stratify_by": ["team_id", "language"], "per_stratum_size": 1},
        {"risk_flags": list(FLAGS), "risk_cap": 1},
        {"risk_flags": ["evidence_partial"], "risk_cap": 500},
        {"random_size": 1, "scope": {"team_id": [f"t{n}" for n in range(20)]}},
        {"random_size": 1, "scope": {key: ["x"] for key in KEYS}},
    ],
)
def test_config_boundary_values_are_accepted(config):
    _assert_matches(_population(30), config, 1)


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"random_size": 0},
        {"risk_cap": 50},
        {"scope": {"team_id": ["team-a"]}},
        {"random_size": 0, "stratify_by": [], "risk_flags": []},
    ],
)
def test_config_that_cannot_select_anything_is_empty(config):
    assert _config_code(config) == "sampling_config_empty"


@pytest.mark.parametrize("seed", [-1, MAX_SEED + 1, True, False, 1.0, "1", None])
def test_bad_seed_is_refused(seed):
    rs = _rs()
    items = _items(_population(5))
    assert _error(lambda: rs.draw_sample(items, {"random_size": 1}, seed=seed)) == "seed_invalid"


@pytest.mark.parametrize("seed", [0, 1, MAX_SEED])
def test_seed_boundaries_are_accepted(seed):
    _assert_matches(_population(5), {"random_size": 2}, seed)


def test_seed_is_keyword_only():
    rs = _rs()
    with pytest.raises(TypeError):
        rs.draw_sample(_items(_population(3)), {"random_size": 1}, 5)


def test_bad_items_are_refused_and_their_closest_valid_forms_are_accepted():
    rs = _rs()

    def code(specs):
        return _error(lambda: rs.draw_sample(_items(specs), {"random_size": 1}, seed=1))

    assert code([("a", {"region": "north"}, ())]) == "item_invalid"
    assert code([("a", {"team_id": "t"}, ("needs_review",))]) == "item_invalid"
    assert code([("a", {}, ()), ("a", {"team_id": "t"}, ())]) == "duplicate_item"
    _assert_matches(
        [
            ("a", {key: "v" for key in KEYS}, FLAGS),
            ("b", {key: None for key in KEYS}, ()),
            ("c", {}, ()),
        ],
        {"random_size": 3},
        1,
    )


def test_sample_item_defaults_to_no_strata_and_no_flags():
    rs = _rs()
    draw = rs.draw_sample(
        [rs.SampleItem("solo")],
        {"random_size": 1, "stratify_by": ["team_id"], "per_stratum_size": 1},
        seed=2,
    ).to_json()
    assert draw["selections"] == [
        {
            "item_id": "solo",
            "reasons": ["stratified", "random"],
            "stratum": {"team_id": UNASSIGNED},
            "risk_flags": [],
        }
    ]


def test_empty_population_draws_nothing_and_reports_the_shortfall():
    draw = _draw([], {"random_size": 3}, 0)
    assert draw["selections"] == [] and draw["population_count"] == 0
    assert draw["scoped_count"] == 0 and draw["risk_flagged_count"] == 0
    assert draw["population_digest"] == _sha([])
    assert draw["selection_digest"] == _sha([])
    assert draw["shortfalls"] == [
        {"method": "random", "stratum": None, "requested": 3, "selected": 0}
    ]


def test_package_has_a_readme_naming_the_reproduction_limit_and_later_flags():
    readme = (PACKAGE_DIR / "README.md").read_text(encoding="utf-8")
    assert "population_digest" in readme
    assert "needs_review" in readme and "review_required" in readme
    assert "—" not in readme


def test_package_is_pure():
    forbidden = ("sqlalchemy", "fastapi", "httpx", "requests", "redis", "celery", "boto3")
    inner = ("backend.database", "backend.routers", "backend.services", "backend.config")
    sources = sorted(PACKAGE_DIR.rglob("*.py"))
    assert sources
    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                assert name.split(".")[0] not in forbidden, (source.name, name)
                assert not name.startswith(inner), (source.name, name)
