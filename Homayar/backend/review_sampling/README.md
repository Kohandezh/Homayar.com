# Review sampling (`backend.review_sampling`)

HOM-72 (WP14-T01, feature F43). This package picks conversations for human review. It
has three methods: risk-triggered, stratified and random. Every draw is reproducible from
a recorded seed and the sampler version.

- It is pure: no database, no network, no file I/O.
- It is deterministic: the same items, config and seed always give the same result. The
  order of the items never changes it.
- It never guesses. A bad input raises `SamplingError` with a `code`.

The version is `review-sampling/1` (`SAMPLER_VERSION`). The algorithm below is part of the
version. A change to the rank, the order, a limit or a digest is a new version.

The admin endpoint `POST /api/v1/admin/review-samples` uses this package. See "Use in
the backend" at the end.

## Usage

```python
from backend.review_sampling import SampleItem, draw_sample

items = [
    SampleItem("m1", strata={"team_id": "t1", "language": "fa"}, risk_flags=["evidence_partial"]),
    SampleItem("m2", strata={"team_id": "t2"}),
]
draw = draw_sample(items, {"random_size": 10, "risk_flags": ["evidence_partial"]}, seed=42)
draw.to_json()  # JSON-safe, see "Result"
```

## Items

`SampleItem(item_id, strata={}, risk_flags=())` is one candidate.

- `item_id` must be a `str`.
- `strata` keys must be in `STRATUM_KEYS` = `agent_user_id`, `language`, `source`,
  `team_id`. Each value is a `str` or `None`. A missing key or `None` means
  `UNASSIGNED` (`"unassigned"`). `item.strata` is a read-only mapping of every key.
- `risk_flags` must be a list of values from `RISK_FLAGS` = `evidence_partial`,
  `speaker_attribution_unknown`, `transcript_corrected`. A repeated flag counts once. The
  item keeps its flags sorted.

Anything else raises `SamplingError` `item_invalid` (the constructor checks). An entry of
`items` that is not a `SampleItem` is also `item_invalid`. Two items with the same
`item_id` give `duplicate_item`.

## Config

`SamplingConfig` is a frozen model. `draw_sample` also accepts a plain dict. Every
validation path (the constructor, `model_validate`, `model_validate_json`,
`model_validate_strings`) raises `SamplingError`, never a bare pydantic error.

| Field | Type, default | Rule |
| --- | --- | --- |
| `random_size` | int, 0 | 0 to 500 |
| `stratify_by` | list of distinct `STRATUM_KEYS`, `[]` | 0 to 2 keys |
| `per_stratum_size` | int, 0 | 0 to 100; above 0 exactly when `stratify_by` is not empty |
| `risk_flags` | list of distinct `RISK_FLAGS`, `[]` | |
| `risk_cap` | int, 100 | 1 to 500 |
| `scope` | `{stratum_key: [values]}`, `{}` | keys in `STRATUM_KEYS`, each list 1 to 20 distinct strings |

Types are strict: `True`, `"5"` or `5.0` is not an int. Unknown keys are refused. A rule
break gives `sampling_config_invalid`. A config where no method can select anything
(`random_size` 0, `stratify_by` empty, `risk_flags` empty) gives `sampling_config_empty`.

The normalized config (`SamplingConfig.normalized()`) has every field. `stratify_by`,
`risk_flags`, the `scope` keys and each scope list are sorted.

## Seed

`seed` must be an `int` (a `bool` is refused) from 0 to `MAX_SEED` = `2**53 - 1`, else
`seed_invalid`. `2**53 - 1` is the largest integer a JSON reader in a browser keeps exact.

`draw_sample` checks in this order: config, seed, items.

## Algorithm

1. **Scope.** An item is in scope when, for every scope key, its value for that key (or
   `UNASSIGNED`) is in the key's list. An item out of scope is never selected.
2. **Rank.** For a method `m` in `risk`, `stratified`, `random`:
   `rank(m, item) = sha256(f"{m}|{seed}|{item_id}".encode("utf-8")).hexdigest()`. The lower
   hex string comes first. Each method has its own rank, so the methods do not pick the
   same items by design.
3. **risk.** The in-scope items with at least one flag from `config.risk_flags`, in rank
   order, the first `risk_cap`.
4. **stratified.** A stratum is the tuple of an item's values for the sorted
   `stratify_by` keys. For each stratum that has in-scope items, the first
   `per_stratum_size` items of that stratum in rank order.
5. **random.** The first `random_size` in-scope items in rank order.

Each method draws from all in-scope items. It does not depend on the other methods.

## Result

`draw_sample(items, config, *, seed)` returns a `SampleDraw`. `to_json()` gives:

```json
{
  "sampler_version": "review-sampling/1",
  "seed": 42,
  "config": {"random_size": 10, "stratify_by": [], "per_stratum_size": 0, "risk_flags": [], "risk_cap": 100, "scope": {}},
  "config_digest": "<64 hex>",
  "population_count": 120,
  "scoped_count": 80,
  "population_digest": "<64 hex>",
  "risk_flagged_count": 0,
  "selections": [{"item_id": "m1", "reasons": ["random"], "stratum": {}, "risk_flags": []}],
  "selection_digest": "<64 hex>",
  "shortfalls": []
}
```

- `population_count`: all given items. `scoped_count`: the items in scope.
- `risk_flagged_count`: in-scope items with a configured flag. It can be above
  `risk_cap`.
- `selections`: one entry per selected item. Order: risk picks in rank order, then
  stratified picks (strata in sorted order of their value tuples, then rank), then random
  picks in rank order. An item picked again keeps its first place and gets the new
  reason. `reasons` is a subset of `["risk", "stratified", "random"]`, in that order.
  `stratum` is `{key: value}` for the `stratify_by` keys (`UNASSIGNED` for none), or `{}`
  when `stratify_by` is empty. `risk_flags` is all of the item's flags, sorted.
- `shortfalls`: a method that found fewer items than requested. One entry per stratum
  with fewer in-scope items than `per_stratum_size`:
  `{"method": "stratified", "stratum": {...}, "requested": n, "selected": k}`. Then, when
  the in-scope count is below `random_size`:
  `{"method": "random", "stratum": null, "requested": n, "selected": k}`. The order
  follows the method order of `selections`. A stratum with no in-scope item is not
  reported. The risk method has no requested size, so it has no shortfall.

## Digests

Each digest is the sha256 hex of canonical JSON: sorted keys, separators `(",", ":")`,
`ensure_ascii=False`, UTF-8. This is the same form as `taxonomy_digest`.

- `config_digest`: the normalized config. `config_digest(config)` is public.
- `population_digest`: every given item, in and out of scope, as a list sorted by
  `item_id` of `{"item_id", "strata": {every STRATUM_KEY: value or null}, "risk_flags":
  sorted list}`. `population_digest(items)` is public.
- `selection_digest`: the list `[[item_id, reasons], ...]` in selection order.

## Reproducing a draw

Send the same seed and the normalized config again, over the same population. The draw is
the same, so `selection_digest` is the same.

The limit: a later draw gives the same items only while the population is unchanged. New
calls, a deleted call or a changed risk flag change the population. A different
`population_digest` shows that it changed. The draw itself is still correct for the new
population.

## Coverage counters (`coverage/1`)

`GET /api/v1/admin/coverage` (`backend/services/coverage_service.py`) gives four separate
counters. There is no blended number. Each counter has a definition, a numerator, a
denominator and buckets. Each denominator is the numerator of the counter before it:

| Counter | Numerator / denominator |
| --- | --- |
| `ingestion` | `valid` (accepted) / `ingested` (every ledger row in the window) |
| `processing` | `transcribed` / `valid` |
| `evaluation` | `fully_evaluated` / `transcribed` |
| `review` | `reviewed` / `fully_evaluated` (no source yet: `review_records_unavailable`) |

The population is the ingestion ledger (`ingestion_records`) of the workspace, with
`from <= received_at < to`. Two facts follow from that:

- A meeting with no ledger row (legacy or demo seed data) is not counted.
- A transient refusal (for example a full quota or a rate limit) writes no ledger row, so
  it is not counted either. Only a payload rejection (`REJECTED` with a `reason`) is.

A failed or cancelled call is never `transcribed`, so it can never count as fully
evaluated, even when it has a transcript and a completed analysis. "Fully evaluated" is
exactly the call report's `completeness.is_complete`.

## Use in the backend

`backend/services/review_sampling_service.py` builds the items for
`POST /api/v1/admin/review-samples`:

- The population is exactly the meetings in the `fully_evaluated` bucket of the coverage
  counters (`coverage/1`) for the request window. One item per meeting; `item_id` is the
  meeting id.
- Strata: `team_id`, `agent_user_id`, `source`, `language` of the meeting.
- The endpoint is stricter than this package about scope values. Each value must be
  `unassigned` or have the shape of its meeting column: a lowercase UUID for `team_id` and
  `agent_user_id`, `^[a-z][a-z0-9_]{0,29}$` for `source`,
  `^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})?$` for `language`. Anything else is
  `422 sampling_config_invalid`. Why: the scope is stored in the audit row under keys the
  audit sanitizer does not mask, so free text (a phone number, an email) must never get
  there.
- `seed` is optional. An absent seed and `"seed": null` are the same: the server picks a
  seed with `secrets` (0 to `MAX_SEED`) and returns it in the draw.
- Risk flags (v1, from data that exists today):
  - `evidence_partial`: the meeting's aggregate evidence is `partial`;
  - `speaker_attribution_unknown`: speaker attribution is `unknown`;
  - `transcript_corrected`: the current transcript revision has source `correction`.

The endpoint records each draw as one `review_sample.drawn` audit event with the seed,
both versions, the window, the normalized config and the three digests. Storing the
selected items as review work is HOM-73.

Flags expected later, once their results are stored (a new sampler version adds them):

- `needs_review` from the analysis taxonomy policy (`analysis_taxonomy`);
- `review_required` from the conversation checks.
