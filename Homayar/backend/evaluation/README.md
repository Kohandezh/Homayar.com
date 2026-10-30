# Evaluators (`backend.evaluation`)

HOM-69 (WP13-T05, features F80 and F81). This package scores system output against gold
annotations. It covers speech recognition (WER, CER), diarization (DER, JER), intent
classification, entity extraction, QA rater agreement, factual support of generated
claims, and retrieval.

- It is pure: standard library only, plus `backend.speech` (`benchmark`,
  `text_normalize`). No database, no network, no provider, no language model.
- File I/O happens only in `loaders.py` and the CLI.
- It is deterministic: the same input gives the same output. Nothing is random.
- When a metric cannot be measured, its value is None and a reason code says why. It
  never reports 0 as a stand-in.
- Every evaluator accepts an empty input. It returns None values with a reason, and does
  not raise.

The real WP13 gold corpus does not exist yet. The tests use small synthetic fixtures in
`backend/tests/fixtures/evaluation/`. The corpus plugs in later through the JSONL loaders
and the CLI, with no code change.

The version is `evaluation/1` (`EVALUATION_VERSION`). A change to any definition below is a
new version.

Import everything from `backend.evaluation`.

## Usage

```python
from backend.evaluation import evaluate_by_slice, evaluate_diarization, load_jsonl

items = load_jsonl("diarization", "gold/diarization.jsonl")
report = evaluate_diarization(items, collar_ms=250)
report.metrics["der"].value
by_slice = evaluate_by_slice(items, evaluate_diarization, ["channel", "dialect"])
report.as_dict()  # JSON-safe
```

## Common types

`MetricValue` is one metric:

| Field | Meaning |
| --- | --- |
| `name` | the metric name |
| `value` | `numerator / denominator`, or None when not measurable |
| `numerator`, `denominator` | the two terms behind `value` |
| `n_items` | units that were scored |
| `n_excluded` | input units that were not scorable (meaning per metric below) |
| `n_abstained` | units where the system abstained (meaning per metric below) |
| `reason` | a reason code when `value` is None, else None |

When `value` is not None, `value == numerator / denominator`. When `value` is None,
`reason` is a non-empty string.

Every report is a frozen dataclass with `metrics: dict[str, MetricValue]` and
`as_dict()`. The result of `as_dict()` passes `json.dumps`.

`evaluate_by_slice(items, evaluate, dimensions)` returns `{"all": evaluate(all items)}`
plus one key `"<dim>=<value>"` for each value of each requested dimension found in
`item.slices`. Items keep their input order in each slice. An item without a dimension is
only in `"all"`. `"all"` is there even for empty input. Every item type has a `slices`
mapping of string to string (default empty).

## ASR: WER and CER

`evaluate_asr(utterances)` returns `AsrReport` with `metrics` (`wer`, `cer`) and `counts`
(`ErrorCounts`, summed over scored utterances).

- It reuses `backend.speech.benchmark`. `Utterance` is `benchmark.Utterance`. Each
  utterance is scored with `benchmark.score_pair`, after `normalize_for_matching` for its
  language. So orthographic variants (`ي`/`ی`, Persian digits, ZWNJ) are not errors.
- `wer = sum(word_edits) / sum(ref_words)`, `cer = sum(char_edits) / sum(ref_chars)`.
  These are corpus rates, not a mean of per-utterance rates.
- An utterance whose normalised reference has no words is not scorable. It counts in
  `n_excluded` and adds nothing. This is the same rule as `benchmark.aggregate`.

## Diarization: DER and JER

`score_diarization(item, *, collar_ms=0, skip_overlap=False)` scores one recording.
`evaluate_diarization(items, ...)` scores a corpus.

A `DiarizationItem` has `recording_id`, `reference` and `hypothesis` (lists of
`SpeakerTurn(speaker, start_ms, end_ms)`), and an optional `scored_region`
`(start_ms, end_ms)`. All time arithmetic is in integer milliseconds. A turn covers
`[start_ms, end_ms)`.

The steps:

1. Turns of the same speaker that overlap or touch are merged, separately in the
   reference and in the hypothesis.
2. The scored region is `scored_region`, or `[0, last turn end)` when it is None. Speech
   outside it is ignored.
3. Collar: for each merged reference turn, `[start - collar, start + collar)` and
   `[end - collar, end + collar)` are removed from the scored region (clipped at 0).
4. `skip_overlap=True` removes every instant with two or more reference speakers.
5. The rest is split at every boundary. For a piece of length `d`, with `N_ref` and
   `N_hyp` active speakers and `N_correct` reference speakers whose mapped hypothesis
   speaker is also active:
   - `missed_ms += max(0, N_ref - N_hyp) * d`
   - `false_alarm_ms += max(0, N_hyp - N_ref) * d`
   - `confusion_ms += (min(N_ref, N_hyp) - N_correct) * d`
   - `reference_ms += N_ref * d` (overlapped speech counts once per speaker)
6. DER mapping: the one-to-one reference to hypothesis mapping with the largest total
   co-occurrence time in the scored region. It is an optimal assignment (Hungarian method,
   `assignment.py`), not a greedy one, and it works for unequal speaker counts. Only pairs
   that co-occur appear in `mapping`.
7. `der = (missed_ms + false_alarm_ms + confusion_ms) / reference_ms`.
8. JER (DIHARD style): for reference speaker `r` (duration `R`) and hypothesis speaker `h`
   (duration `H`, intersection `I`), the error is `1 - I / (R + H - I)`. A second optimal
   assignment minimises the total error. An unmapped reference speaker has error 1.0.
   `jer` is the mean over reference speakers with speech in the scored region. The JER
   arithmetic uses exact fractions.
9. Speaker counts are the labels with any speech inside the scored region, before collar
   and overlap removal. `speaker_count_error = n_hypothesis_speakers -
   n_reference_speakers`.

Reference speech with no hypothesis speech gives `der == 1.0` and `jer == 1.0`.

Corpus metrics:

| Metric | Value | Notes |
| --- | --- | --- |
| `der` | sum of errors / sum of `reference_ms` | weighted by reference time; `n_items` = recordings |
| `jer` | mean of per-recording `jer` | `n_excluded` = recordings with `jer` None |
| `speaker_count_exact` | recordings with `speaker_count_error == 0` / recordings | |

`DiarizationReport` also has the corpus totals `missed_ms`, `false_alarm_ms`,
`confusion_ms`, `reference_ms`, and `scores` (one `DiarizationScore` per recording, in
input order).

Invalid input raises `ValueError`: a turn bound that is not an int, `start_ms < 0`,
`end_ms <= start_ms`, `collar_ms < 0`, or a `scored_region` with `start < 0` or
`end <= start`. A 1 ms turn and `collar_ms=0` are fine.

## Intent classification

`evaluate_classification(items, *, labels=None)` takes `LabeledItem(gold, predicted)`.

- `gold` None means the right behaviour is to abstain (a contradictory fixture).
  `predicted` None means the system abstained.
- `labels` is the supported taxonomy. When None, it is the sorted union of all non-None
  labels. A gold label outside a given `labels` raises `ValueError`. A predicted label
  outside it is allowed: it counts in `n_out_of_taxonomy`, is a false negative for the
  gold class, and a false positive for no class.
- Per class `c` (`ClassStats`): `tp` = gold and pred are `c`; `fp` = pred is `c`, gold is
  not (gold None included); `fn` = gold is `c`, pred is not (pred None included).
  `precision = tp / (tp + fp)`, `recall = tp / (tp + fn)`, `f1 = 2tp / (2tp + fp + fn)`.
  Each is None when its denominator is 0.

| Metric | Value | Counts |
| --- | --- | --- |
| `macro_f1` | mean of the defined class `f1` values | `n_excluded` = classes with `f1` None |
| `accuracy` | items with gold not None and pred == gold / items with gold not None | an abstention is wrong; `n_abstained` = those abstentions |
| `abstention_rate` | items with pred None / all items | `n_abstained` = that count |
| `correct_abstention_rate` | items with gold None and pred None / items with gold None | |

## Entities

`evaluate_entities(items, *, match="value", normalize=None)` takes
`EntityItem(gold, predicted)`, lists of `EntityMention(type, value, start, end)`.

- `match="value"`: per item and per type, the multiset intersection of
  `normalize(value)`. Offsets are ignored. `normalize` None means identity.
- `match="span"`: the key is `(type, start, end)`. Values are ignored. A mention with a
  None offset raises `ValueError`.
- `tp` = intersection size, `fp` = predicted - tp, `fn` = gold - tp. Mentions never match
  across items.
- `per_type` holds `ClassStats` per type, sorted. Per-type `recall` is the exact-match
  rate that the critical-entity gate (F27, at least 95%) reads.
- `precision`, `recall`, `f1` are micro over all types.

## QA agreement

`cohen_kappa(pairs, *, categories=None, weights="none")` takes
`RatingPair(rating_a, rating_b, criterion)`. `kappa_by_criterion` runs it per criterion,
sorted by criterion.

- A pair with a None rating is excluded (`n_excluded`).
- `weights` is `none`, `linear` or `quadratic`. `linear` and `quadratic` need
  `categories`, because the order matters. Without `categories`, the categories are the
  sorted observed ratings. A rating outside given `categories` raises `ValueError`.
  `kappa_by_criterion` checks `weights` and `categories` the same way, also on empty
  input.
- `O[i][j]` is the share of pairs rated `(i, j)`. `E[i][j]` is the share of A's `i` times
  the share of B's `j`. Weights: `none` is 0 on the diagonal and 1 elsewhere, `linear` is
  `|i - j| / (k - 1)`, `quadratic` is `((i - j) / (k - 1)) ** 2`. With one category all
  weights are 0.
- `numerator = sum(w * E) - sum(w * O)`, `denominator = sum(w * E)`. With `none` weights
  this is the usual `(p_o - p_e) / (1 - p_e)`.

`score_mae(pairs)` takes `ScorePair(score, reference)` and gives the mean of
`|score - reference|`.

## Factual support

`evaluate_factual_support(claims)` takes `AuditedClaim(claim_id, label, citations, severe,
recording_duration_ms)`. The `label` (`SupportLabel`: `supported`, `unsupported`,
`contradicted`, `not_audited`) comes from a human audit or a separate judge. This module
never decides support itself.

- `citation_is_valid(citation, duration)`: true only when `0 <= start_ms < end_ms`,
  `revision_id` is not blank, and `end_ms <= duration` when the duration is known.
- A claim is structurally valid when it has at least one citation and all are valid.
- `support_rate` = supported / audited claims. `n_excluded` = `not_audited` claims.
- `citation_validity` = structurally valid claims / all claims.
- `invented_severe` counts severe claims labelled `unsupported` or `contradicted`.
  `invented_severe_claim_ids` and `unsupported_claim_ids` list them in input order.

## Retrieval

`evaluate_retrieval(queries, *, ks=(1, 5, 10))` takes `RetrievalQuery(query_id,
relevance, ranked)`. `relevance` maps a doc id to a graded int. Above 0 is relevant, 0 is
judged not relevant, and an unjudged doc counts as 0.

- Duplicate ids in `ranked` keep their first place only, before any cut.
- A repeated value in `ks` is scored once. `ks=(5, 5)` gives the same report as
  `ks=(5,)`.
- A query with no relevant doc is not scorable (`n_excluded`).
- Per query: `recall@k` = relevant in top k / all relevant; `precision@k` = relevant in
  top k / k; `hit@k` = 1 if any relevant in top k; reciprocal rank = 1 / position of the
  first relevant doc (0 when none); `ndcg@k = DCG@k / IDCG@k` with gain
  `(2 ** rel - 1) / log2(i + 1)` at position `i`, and IDCG from all relevance values
  sorted high to low.
- Each metric (`mrr`, and `recall@k`, `precision@k`, `hit@k`, `ndcg@k` for each k) is the
  mean over scored queries. A scored query with an empty ranking scores 0 and counts in
  `n_abstained`.
- A `k` that is not a positive int, or a negative relevance, raises `ValueError`.

## Reason codes

| Code | Metrics | Meaning |
| --- | --- | --- |
| `no_reference_words` | `wer`, `cer` | no utterance had reference words |
| `no_reference_speech` | `der`, `jer`, per-recording `der`/`jer` | no reference speech in the scored region |
| `no_recordings` | `speaker_count_exact` | no recording |
| `no_labels` | `macro_f1` | no class has a defined F1 |
| `no_gold_labels` | `accuracy` | no item has a gold label |
| `no_items` | `abstention_rate` | no item |
| `no_abstention_fixtures` | `correct_abstention_rate` | no item has gold None |
| `no_entities` | entity `precision`, `recall`, `f1` | no gold or predicted entity in that denominator |
| `no_pairs` | `kappa`, `score_mae` | no pair with both ratings |
| `no_expected_disagreement` | `kappa` | `sum(w * E)` is 0, for example both raters used one category |
| `no_audited_claims` | `support_rate` | every claim is `not_audited`, or there is none |
| `no_claims` | `citation_validity` | no claim |
| `no_relevant_documents` | retrieval metrics | no query has a relevant document |
| `non_finite_result` | any metric, and per-recording `der` | the inputs are finite, but the numerator, denominator or value overflowed in float math, or an int is too large for a float. Examples: a `score_mae` sum of two `1e308` errors, a retrieval relevance of `1100` (the gain `2 ** 1100 - 1` has no float, so only `ndcg@k` is affected). `value`, `numerator` and `denominator` are all None |

## JSONL input

One JSON object per line, UTF-8. Blank lines are skipped. Unknown keys are ignored, so
newer exports still load. A missing required key, a wrong type, a value out of range,
bad JSON or bad UTF-8 raises `ValueError` with the 1-based line number. Every kind takes
an optional `slices` object of string to string.

The loader checks these ranges itself, so the error names the line: turns need
`0 <= start_ms < end_ms`, `scored_region` needs `0 <= start < end`, and relevance values
need to be `>= 0`. A non-finite number (`NaN`, `Infinity`, `-Infinity`, or a number too
large for a float) anywhere in a record is refused. The evaluators keep the same checks
for Python callers.

`load_jsonl(kind, path)` reads a file. `parse_record(kind, record)` reads one object.
`KINDS` lists the kinds.

| kind | required keys | optional keys | item |
| --- | --- | --- | --- |
| `asr` | `reference`, `hypothesis` | `language` (default `fa`), `slices` | `Utterance` |
| `diarization` | `recording_id`, `reference`, `hypothesis` (lists of `{speaker, start_ms, end_ms}`) | `scored_region` (`[start_ms, end_ms]`), `slices` | `DiarizationItem` |
| `intent` | `gold`, `predicted` (string or null) | `item_id`, `slices` | `LabeledItem` |
| `entities` | `gold`, `predicted` (lists of `{type, value}` with optional `start`, `end`) | `item_id`, `slices` | `EntityItem` |
| `qa_agreement` | `rating_a`, `rating_b` (string or null) | `criterion`, `item_id`, `slices` | `RatingPair` |
| `qa_score` | `score`, `reference` (numbers) | `item_id`, `slices` | `ScorePair` |
| `factual_support` | `claim_id`, `label` (a `SupportLabel` value) | `citations` (list of `{start_ms, end_ms, revision_id}`), `severe`, `recording_duration_ms`, `slices` | `AuditedClaim` |
| `retrieval` | `query_id`, `relevance` (object of doc id to int), `ranked` (list) | `slices` | `RetrievalQuery` |

In a citation, `revision_id` may be left out. The citation then loads, and counts as not
valid in `citation_validity`.

## CLI

Run from `Homayar/`:

```bash
uv run python -m backend.evaluation <kind> <path.jsonl> [options]
```

| Option | Kind |
| --- | --- |
| `--slices dim1,dim2` | all |
| `--collar-ms N`, `--skip-overlap` | `diarization` |
| `--labels a,b,c` | `intent` |
| `--match value\|span`, `--normalize none\|fa\|en` | `entities` (`fa` and `en` use `normalize_for_matching`) |
| `--categories a,b,c`, `--weights none\|linear\|quadratic` | `qa_agreement` |
| `--ks 1,5,10` | `retrieval` |

The output is one JSON document on stdout, with sorted keys:

- `version`: `EVALUATION_VERSION`
- `kind`
- `input_sha256`: SHA-256 of the input file bytes
- `options`: the effective options for the kind
- `slices`: slice key to the report's `as_dict()`. For `qa_agreement` the report is
  `{"kappa": ..., "by_criterion": {...}}`. For `qa_score` it is `{"score_mae": ...}`.

Exit codes: 0 on success. 1 on bad data, with one line on stderr. 2 on bad arguments,
which include a `--ks` value below 1, an empty `--ks`, and `--weights linear` or
`quadratic` without `--categories`. A repeated `--ks` value is used once. The output is
written with `allow_nan=False`. The CLI never prints a traceback for bad data: any
`ValueError` or `ArithmeticError` while loading, evaluating or writing the JSON gives one
line on stderr and exit 1. Two runs on the same file and options print the same
bytes.

## Feeding the WP13 corpus

1. Export each annotation layer to the JSONL shape of its kind above. Put the slice
   dimensions (dialect, noise band, channel, and so on) in `slices`.
2. Run the CLI per kind, or call `load_jsonl` and the evaluator from code.
3. Keep the CLI output: `input_sha256` and `version` tie a result to its exact input and
   definitions.

Not in this package:

- Frozen-split hashing, thresholds and promotion gates belong to HOM-71.
- Confidence intervals, bootstrap, latency and cost belong to HOM-70.
- Wiring into the diarization pipeline and the published collar policy belong to HOM-50.
