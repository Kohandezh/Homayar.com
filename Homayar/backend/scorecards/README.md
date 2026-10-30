# Scorecards (`backend.scorecards`)

HOM-54 (WP11-T04, feature F41). A scorecard is a list of weighted criteria and a pass
threshold. Each criterion wraps exactly one conversation check of
`backend.conversation_checks` (HOM-55). This package:

- validates a scorecard definition;
- decides which criteria apply to one conversation (by its type and direction);
- turns the `CheckRun` of that conversation into criterion outcomes, a score and a
  scorecard outcome;
- gives the rating pairs and score pairs that the HOM-69 agreement metrics read.

The engine supports four outcomes: `passed`, `failed`, `not_applicable` and
`insufficient_evidence`. The score uses a weighted denominator that leaves out the criteria
that are not applicable. Missing evidence never becomes a pass.

- It is pure: no database, no network, no provider call, no file I/O, no clock, no
  randomness. It imports only `backend.conversation_checks` and `backend.evaluation`.
- It is deterministic: the same input always gives the same output.
- It never changes its inputs.

The version is `scorecards/1` (`SCORECARDS_VERSION`). Import everything from
`backend.scorecards`.

Published versions are stored in the `scorecard_versions` table (append-only) through
`services/scorecard_service.py` and the `/api/v1/scorecards` routes. Results are not
stored yet (HOM-56).

## Usage

```python
from backend.conversation_checks import CheckTurn, finish_checks, plan_checks
from backend.scorecards import ConversationFacts, plan_scorecard, score_conversation

definition = {
    "name": "Inbound sales",
    "pass_threshold": 80,
    "criteria": [
        {"criterion_id": "greeting", "label": "Greets the customer", "weight": 40,
         "check": {"check_id": "greeting", "version": 1, "kind": "required_phrase",
                   "phrases": ["سلام"]}},
        {"criterion_id": "fee_explained", "label": "Explains the fee", "weight": 60,
         "na_allowed": True,
         "check": {"check_id": "fee_explained", "version": 1, "kind": "contextual",
                   "question": "Did the agent explain the cancellation fee?",
                   "trigger_phrases": ["لغو"]}},
    ],
}
conversation = ConversationFacts("call", "inbound")  # the HOM-58 columns of the meeting

checks = plan_scorecard(definition, conversation)
plan = plan_checks(checks, turns, language="fa")
run = finish_checks(plan, answers, evaluator=evaluator)  # see conversation_checks
result = score_conversation(definition, run, conversation)
result.to_json()  # plain JSON, see "The result shape"
```

## Constants (version 1)

| Name | Value | Meaning |
| --- | --- | --- |
| `SCORECARDS_VERSION` | `scorecards/1` | the version of every rule here |
| `CONVERSATION_TYPES` | `call`, `meeting`, `voicemail`, `other` | a copy of `ConversationType` (HOM-58) |
| `CONVERSATION_DIRECTIONS` | `inbound`, `outbound`, `internal` | a copy of `ConversationDirection` (HOM-58) |
| `KEY_PATTERN` | `^[a-z][a-z0-9_]{1,63}$` | a criterion id, and the scorecard key of the API |
| `REVIEWER_OUTCOMES` | `passed`, `failed`, `not_applicable` | what a person may confirm |

The two value copies exist so this package does not import the database. A test keeps
them equal to the enums. A test pins the version. A change to any rule here is a new
version.

## The definition

`ScorecardDefinition` is a frozen pydantic model. Unknown keys are refused. Every function
that takes a definition also accepts a plain dict.

| Field | Rule | Default |
| --- | --- | --- |
| `name` | string, 1 to 200 characters after `strip()`; stored as given | required |
| `description` | string of at most 2000 characters, or null | null |
| `pass_threshold` | int from 1 to 100 (a percent); `bool` is refused | required |
| `criteria` | a list of 1 to 100 criteria | required |

`Criterion`:

| Field | Rule | Default |
| --- | --- | --- |
| `criterion_id` | matches `KEY_PATTERN`, unique in the scorecard | required |
| `label` | string, 1 to 200 characters after `strip()` | required |
| `weight` | int from 0 to 100; `bool` is refused | required |
| `auto_fail` | bool. A confirmed failure of this criterion fails the whole scorecard | `false` |
| `na_allowed` | bool. The criterion may be not applicable (approved NA) | `false` |
| `applies_to` | an `Applicability`, or null for every conversation | null |
| `check` | one conversation check: a `PhraseCheck`, a `ContextualCriterion`, or a dict that `plan_checks` accepts | required |

`Applicability`:

| Field | Rule | Default |
| --- | --- | --- |
| `conversation_types` | null, or a non-empty list of distinct `CONVERSATION_TYPES` values | null (all) |
| `directions` | null, or a non-empty list of distinct `CONVERSATION_DIRECTIONS` values | null (all) |

An `Applicability` with both fields null is refused: use `applies_to: null` instead. An
enum member (for example `ConversationType.CALL`) is read as its value and stored as a
plain string.

`validate_definition(value)` is the entry point. It also validates a model instance again,
because `model_copy(update=...)` and `model_construct` skip validation. A check given as a
model is validated again the same way.

## Errors

Every error is a `ScorecardError`, a `ValueError` with a `code`.

| Code | When | Must still pass |
| --- | --- | --- |
| `scorecard_invalid` | the definition is not a `ScorecardDefinition` or a dict; a top-level field breaks a rule above; an unknown top-level key | a minimal valid definition |
| `criterion_invalid` | a criterion field breaks a rule above; an unknown key in a criterion or in `applies_to`; a criterion that is not a dict | |
| `duplicate_criterion` | two criteria have the same `criterion_id` | |
| `weights_invalid` | the weights do not add up to exactly 100 | a sum of exactly 100 |
| `weight_zero_not_auto_fail` | a criterion has weight 0 and `auto_fail` false | weight 0 with `auto_fail` true |
| `check_invalid` | the check is not a valid conversation check (any error of `conversation_checks` validation) | a valid `PhraseCheck` as a model, and the same check as a dict |
| `check_id_mismatch` | `check.check_id` is not the `criterion_id` | equal ids |
| `na_not_allowed` | the check is `contextual` with trigger phrases and `na_allowed` is false (a trigger check can be `not_applicable`) | the same check with `na_allowed` true; a contextual check without triggers |
| `conversation_invalid` | the conversation facts break the rules in "Applicability" | |
| `check_run_mismatch` | the check run does not match the plan (see "Scoring") | |
| `reviewer_outcome_invalid` | a reviewer outcome names an unknown criterion, has a value outside `REVIEWER_OUTCOMES`, or names a criterion that is not applicable by conversation | a reviewer outcome for a criterion that applies, or whose applicability is unknown |
| `reviewer_na_not_allowed` | a reviewer outcome `not_applicable` for a criterion with `na_allowed` false | |
| `definition_mismatch` | `rating_pairs` gets two results of different definitions | |
| `result_invalid` | `rating_pairs` or `score_pair` gets something that is not a `ScorecardResult` or its `to_json()` dict, or a reference score that is not a finite number (this package's own code) | |

When several rules are broken, which code is reported first is not fixed. Each broken rule
alone raises its own code. The model constructors (`ScorecardDefinition(...)`,
`Criterion(...)`, `Applicability(...)`) raise `ScorecardError` too. Pydantic's own class
methods (`model_validate`, `model_validate_json`) still raise its `ValidationError`; both
are a `ValueError`.

## Applicability

`ConversationFacts(conversation_type, direction)` is a frozen dataclass. A dict with exactly
these two keys is accepted too. `conversation_type` is a `CONVERSATION_TYPES` value.
`direction` is a `CONVERSATION_DIRECTIONS` value or `None` (not known). Anything else is
`conversation_invalid`.

A criterion, for one conversation, in this order:

| Case | Applicability | Reason |
| --- | --- | --- |
| `applies_to` is null | applies | |
| `conversation_types` is given and the type is not in it | not applicable | `not_applicable_by_conversation` |
| `directions` is given and the direction is `None` | unknown | `direction_unknown` |
| `directions` is given and the direction is not in it | not applicable | `not_applicable_by_conversation` |
| otherwise | applies | |

The type is decided before the direction. So a voicemail checked by a criterion for calls
is not applicable, even when the direction is not known.

## Planning

`plan_scorecard(definition, conversation)` returns the checks of the criteria that apply,
in criterion order, as a list. Pass them to `conversation_checks.plan_checks` and
`finish_checks`. A criterion that is not applicable or unknown has no check run.

## Scoring

`score_conversation(definition, check_run, conversation, reviewer_outcomes=None)` returns a
`ScorecardResult`.

`check_run` is a `CheckRun` or its `to_json()` dict. It must match the plan exactly, else
`check_run_mismatch`:

- `checks_version` is `conversation-checks/1` (`conversation_checks.CHECKS_VERSION`);
- the `check_id`s of the results are exactly the criteria that apply: none missing, none
  extra, none twice;
- each result has the `check_version` and `definition_digest` of its criterion's check;
- each result has the fields of `CheckResult.to_json()` with their types (`reason` null
  for `passed` and `failed`, a code otherwise; `decided_by` `rule` or `model` for `passed`
  and `failed`, null otherwise).

`reviewer_outcomes` is null or a mapping from `criterion_id` to a `REVIEWER_OUTCOMES`
value: a decision a person confirmed (HOM-18 supplies it later).

### Criterion outcome, in this order

| # | Case | `outcome` | `reason` | `decided_by` |
| --- | --- | --- | --- | --- |
| 1 | not applicable by conversation | `not_applicable` | `not_applicable_by_conversation` | `rule` |
| 2 | a reviewer outcome exists | the reviewer value | null for passed and failed; `reviewer_not_applicable` for not_applicable | `reviewer` |
| 3 | applicability unknown | `insufficient_evidence` | `direction_unknown` | null |
| 4 | the check result has `review_required` true (any check outcome) | `insufficient_evidence` | `review_required` | null |
| 5 | check outcome `passed` | `passed` | null | the check's `decided_by` |
| 6 | check outcome `failed` | `failed` | null | the check's `decided_by` |
| 7 | check outcome `not_applicable` and `na_allowed` true | `not_applicable` | the check's reason (for example `not_triggered`) | `rule` |
| 8 | check outcome `not_applicable` and `na_allowed` false | `insufficient_evidence` | `na_not_allowed` | null |
| 9 | check outcome `not_computable` | `insufficient_evidence` | the check's reason (for example `no_transcript`) | null |

Row 8 cannot happen for a valid definition today (a trigger check needs `na_allowed`), but
the engine still handles it this way.

### Weights and score

Every decision uses integer math. No float decides an outcome.

| Name | Sum of the weights of the criteria that are |
| --- | --- |
| `applicable_weight` | not `not_applicable` |
| `earned_weight` | `passed` |
| `failed_weight` | `failed` |
| `insufficient_weight` | `insufficient_evidence` |

`complete` is true when no criterion is `insufficient_evidence` (of any weight, also 0).

When `applicable_weight > 0`:

- `score_min` = 100 × `earned_weight` / `applicable_weight`
- `score_max` = 100 × (`earned_weight` + `insufficient_weight`) / `applicable_weight`
- `score` = `score_min` when `complete`, else null

Each is rounded to 2 decimals, half up, from the exact fraction (the same as
`decimal.ROUND_HALF_UP`). Examples: 2 of 3 is `66.67`; 1 of 8 is `12.5`; 1 of 32 (exactly
3.125) is `3.13`, not `3.12`. When `applicable_weight` is 0, all three are null.

### Scorecard outcome, in this order

| # | Case | `outcome` | `reason` |
| --- | --- | --- | --- |
| 1 | every criterion is `not_applicable` | `not_applicable` | `no_applicable_criteria` |
| 2 | an `auto_fail` criterion is `failed` | `failed` | `auto_fail` |
| 3 | `applicable_weight` is 0 and a criterion is `insufficient_evidence` | `insufficient_evidence` | `evidence_missing` |
| 4 | `applicable_weight` is 0 | `not_applicable` | `no_applicable_criteria` |
| 5 | (`earned_weight` + `insufficient_weight`) × 100 < `pass_threshold` × `applicable_weight` | `failed` | `below_threshold` |
| 6 | `earned_weight` × 100 ≥ `pass_threshold` × `applicable_weight`, and no `auto_fail` criterion is `insufficient_evidence` | `passed` | null |
| 7 | otherwise | `insufficient_evidence` | `evidence_missing` |

Why rows 5 and 6 can decide while evidence is missing: row 5 fails only when the call would
fail even if every missing criterion passed. Row 6 passes only when the call would pass
even if every missing criterion failed. So missing evidence never becomes a pass, and a
pass never depends on a criterion that may auto-fail. When `complete` is true, rows 5 and 6
are the plain threshold test.

## The result shape

`ScorecardResult.to_json()`:

```json
{
  "scorecards_version": "scorecards/1",
  "checks_version": "conversation-checks/1",
  "definition_digest": "<64 hex>",
  "conversation": {"conversation_type": "call", "direction": "inbound"},
  "outcome": "passed",
  "reason": null,
  "complete": true,
  "pass_threshold": 80,
  "score": 87.5,
  "score_min": 87.5,
  "score_max": 87.5,
  "applicable_weight": 80,
  "earned_weight": 70,
  "failed_weight": 10,
  "insufficient_weight": 0,
  "criteria": [
    {
      "criterion_id": "greeting",
      "label": "Greets the customer",
      "weight": 10,
      "auto_fail": false,
      "na_allowed": false,
      "outcome": "passed",
      "reason": null,
      "decided_by": "rule",
      "review_required": false,
      "review_reasons": [],
      "check_outcome": "passed",
      "check_reason": null,
      "evidence": [],
      "candidate_evidence": []
    }
  ]
}
```

- `criteria` has one entry per criterion, in definition order.
- `review_required` is true only for row 4. `review_reasons` is then the check's
  `review_reasons`, else `[]`.
- `check_outcome`, `check_reason`, `evidence` and `candidate_evidence` are copied from the
  check result. When no check ran (rows 1 and 3, or a reviewer outcome on a criterion
  without a check result) they are null, null, `[]` and `[]`. With a reviewer outcome on a
  criterion that has a check result, they are still copied.
- Every outcome is `passed`, `failed`, `not_applicable` or `insufficient_evidence`.
- Every list is a JSON array. The result is frozen, and `to_json()` gives new lists each
  time, so changing the JSON never changes the result.

## Versions and the digest

- `definition_digest(definition)` is sha256 hex over the canonical JSON of
  `validate_definition(definition).model_dump(mode="json")`: sorted keys, no spaces
  (`separators=(",", ":")`), `ensure_ascii=False`, UTF-8. It is the same canonical form as
  `conversation_checks.definition_digest`. Defaults are part of the dump, so two
  definitions that differ in any field have different digests. The same definition as a
  model or as a dict has the same digest.
- Every result carries `scorecards_version`, `checks_version` and `definition_digest`.
- A published definition is never changed: a change is a new version of the scorecard
  (`scorecard_versions`, append-only). Never score an old result again with newer rules.

## Feeding HOM-69 evaluation

- `rating_pairs(result_a, result_b)` gives one `evaluation.RatingPair` per criterion of two
  results of the same definition (else `definition_mismatch`). The rating is the criterion
  outcome, except `insufficient_evidence`, which is `None`, so `cohen_kappa` leaves it out.
  `criterion` is the `criterion_id`.
- `score_pair(result, reference_score)` gives an `evaluation.ScorePair`, or `None` when the
  result's score is null.

Both accept a `ScorecardResult` or its `to_json()` dict.

## Not in this version

- No stored results or evidence (HOM-56).
- No helper that builds `CheckTurn`s from transcript rows, and no model call (the wiring
  ticket).
- No sections or groups of criteria, no organization-level scorecards, and no retiring of a
  scorecard.
