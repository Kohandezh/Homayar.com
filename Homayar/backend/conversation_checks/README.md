# Conversation checks (`backend.conversation_checks`)

HOM-55 (WP11-T05, feature F42). This package checks one call against a list of check
definitions. It has two kinds of check:

- **Phrase checks.** A phrase that must be said (a greeting, a disclosure, a script line)
  or a phrase that must not be said. An exact rule decides them. The rule is
  deterministic.
- **Contextual criteria.** A question that needs judgment, for example "Did the agent
  explain the cancellation fee?". A model answers it, but it only sees a small window of
  turns. This is "targeted" evaluation.

The rule also knows when it cannot decide. A match may be negated, quoted, said by the
wrong speaker, said by an unknown speaker, or heard by poor ASR. Such a match is never an
automatic violation. It goes to human review, or to a targeted model question.

- It is pure: no database, no network, no provider call, no file I/O, no clock.
- It is deterministic: the same input always gives the same output.
- It never changes its inputs.

Nothing in the backend uses it yet. HOM-54 scorecards read its results. A later ticket
connects a real evaluator (async) and storage. There is no table, no API and no
migration here.

The version is `conversation-checks/1` (`CHECKS_VERSION`). Import everything from
`backend.conversation_checks`.

## Usage

```python
from backend.conversation_checks import (
    CheckTurn, EvaluatorInfo, PhraseCheck, finish_checks, plan_checks,
)

turns = [
    CheckTurn("سلام، این تماس ضبط می‌شود", "agent", 0, 3000, 0.9),
    CheckTurn("سلام، می‌خواهم اشتراکم را لغو کنم", "customer", 3000, 6000, 0.9),
]
definitions = [
    PhraseCheck(check_id="recording_disclosure", version=1, kind="required_phrase",
                phrases=["این تماس ضبط می‌شود"], critical=True),
    {"check_id": "fee_explained", "version": 1, "kind": "contextual",
     "question": "Did the agent explain the cancellation fee?", "trigger_phrases": ["لغو"]},
]

plan = plan_checks(definitions, turns, language="fa")
# Send each plan.requests[i] to a qualified evaluator, outside this package.
answers = {request.request_id: answer_from_model for request in plan.requests}
run = finish_checks(plan, answers, evaluator=EvaluatorInfo(
    provider="local", model="judge", manifest_id="judge", manifest_version=1,
    manifest_status="qualified"))
run.to_json()  # plain JSON, see "The result shape"
```

With no requests, `finish_checks(plan)` gives the final results.

## Constants (version 1)

| Name | Value | Meaning |
| --- | --- | --- |
| `CHECKS_VERSION` | `conversation-checks/1` | the version of every rule here |
| `MIN_ASR_CONFIDENCE` | `0.6` | a turn with `asr_confidence < 0.6` is low; `None` is not low |
| `MIN_EVALUATOR_CONFIDENCE` | `0.7` | an answer with `confidence < 0.7` or `None` is not used |
| `MAX_WINDOW_TURNS` | `12` | a larger window is never sent to a model |
| `CUE_WINDOW_TOKENS` | `3` | how far a negation cue may be from a match, on each side |

A test pins these values. Changing any of them is a new version.

## Input: turns

`CheckTurn(text, speaker_role=None, start_ms=None, end_ms=None, asr_confidence=None)` is
a frozen dataclass. A plain dict with the same keys is accepted too.

- The index of a turn is its position in the list. All evidence uses this index.
- `speaker_role` is `"agent"`, `"customer"` or `None`. `None` means the speaker is not
  known (no diarization, or the speaker is not mapped to a role).
- `start_ms` and `end_ms` are `None` or an `int >= 0`, with `end_ms >= start_ms`.
- `asr_confidence` is `None` or a number from 0 to 1.
- A bad turn raises `ChecksError` with code `turn_invalid`.

There is no helper that builds turns from `TranscriptSegment` rows yet. The wiring ticket
adds it. It must reuse `speech_metrics.UNKNOWN_SPEAKER_KEYS` for unknown speakers.

## Input: check definitions

Definitions are frozen pydantic models. Unknown keys are refused. `plan_checks` also
accepts plain dicts: a dict with `kind` `required_phrase` or `forbidden_phrase` is
validated as a `PhraseCheck`, and a dict with `kind` `contextual` as a
`ContextualCriterion`. A dict with no `kind`, or another `kind`, is `check_invalid`.

`plan_checks` also validates a model instance again. `model_copy(update=...)` and
`model_construct` skip pydantic validation, so a changed copy could otherwise break a rule
and still run.

Common fields:

| Field | Rule | Default |
| --- | --- | --- |
| `check_id` | matches `^[a-z][a-z0-9_]{1,63}$`, unique in one call | required |
| `version` | int >= 1 | required |
| `critical` | bool | `False` |
| `speaker_role` | `"agent"`, `"customer"` or `"any"` | `"agent"` |
| `scope` | `Scope(kind="whole_call")`, `Scope(kind="opening", max_turns=n)` or `Scope(kind="closing", max_turns=n)` | whole call |

- `opening` is the first n turns of the call, any speaker. `closing` is the last n turns.
  If the call is shorter, the scope is the whole call.
- `max_turns` is an int >= 1 for `opening` and `closing`. It must be absent or `None` for
  `whole_call`.

`PhraseCheck` fields:

- `kind`: `"required_phrase"` or `"forbidden_phrase"`;
- `phrases`: 1 to 32 strings, any-of;
- `model_fallback`: bool, default `False`. When true, a case the rule cannot decide
  becomes a targeted evaluation request.

`ContextualCriterion` fields:

- `kind`: `"contextual"` (required);
- `question`: non-empty text, the criterion the model judges;
- `trigger_phrases`: 0 to 32 strings, default empty. When given, the criterion applies
  only where a trigger is said;
- `context_turns`: int 0 to 3, default 1.

Every phrase and trigger must fold to at least one token. At construction this is checked
against every registered language. `plan_checks` checks it again with the call's language
(for example a phrase of only tatweel folds to a token in English but to nothing in
Persian).

### Errors

Every error is a `ChecksError`, a `ValueError` with a `code`.

| Code | When |
| --- | --- |
| `check_invalid` | a definition breaks a rule above, or is not a check or a dict |
| `duplicate_check` | two definitions in one call have the same `check_id` |
| `language_unsupported` | `language` is not a registered normalizer language (`fa`, `en`, `az` today). It must be the exact registered name: `fa-IR` and `FA` are refused |
| `unknown_request` | `finish_checks` gets an answer whose `request_id` is not in the plan |
| `turn_invalid` | a turn breaks a rule above (this package's own code) |
| `evaluator_invalid` | an `EvaluatorInfo` has a wrong type or an unknown `manifest_status` (this package's own code) |

`plan_checks` and `definition_digest` always raise `ChecksError` for a bad definition.
The model constructors (`PhraseCheck(...)`, `ContextualCriterion(...)`, `Scope(...)`) also
raise `ChecksError` with `check_invalid`. Pydantic's own class methods
(`model_validate`, `model_validate_json`) still raise pydantic's `ValidationError`. Both
are a `ValueError`.

## The matching rule

- The phrase and each turn text are folded with
  `backend.speech.text_normalize.normalize_for_matching(text, language)`, with the call's
  language for both sides. This package has no normalizer of its own.
- A folded text is split on a single space into tokens.
- A match is a whole run of tokens: the phrase's tokens appear together, in order, in one
  turn's tokens.

So these match:

- Arabic `ي` and `ك` against Persian `ی` and `ک`;
- `می خواهم`, `می‌خواهم` (with ZWNJ) and `میخواهم`;
- Persian or Arabic-Indic digits against ASCII digits;
- any letter case in English; punctuation is ignored.

These do not match:

- a part of a token (`call` in `callback`, `سلام` in `سلامت`);
- the same words in another order;
- a phrase split across two turns.

Every match is reported, also overlapping ones. Two phrases that fold the same give one
span, with the first phrase in definition order.

A span is `{turn_index, speaker_role, start_ms, end_ms, phrase, token_start, token_end}`.
`phrase` is the phrase as defined. `token_start` and `token_end` are a half-open range into
`normalize_for_matching(turn.text, language).split(" ")`.

## Doubts: when the rule cannot decide

A match in a turn inside the scope has zero or more doubts. They are always listed in
this order:

| Doubt | When |
| --- | --- |
| `speaker_unknown` | the check's `speaker_role` is not `any` and the turn's role is `None` |
| `wrong_speaker` | only for `required_phrase`: the turn's role is the other known role |
| `low_asr_confidence` | the turn's `asr_confidence` is below `MIN_ASR_CONFIDENCE` |
| `quoted_speech` | the match is inside quote marks, or the turn has a reporting verb |
| `negation` | a negation cue is within `CUE_WINDOW_TOKENS` tokens before or after the match, in the same turn |

A match with no doubt is **clean**.

For `forbidden_phrase`, a match in a turn of the other known role is ignored. It is
neither clean nor a candidate. For example, the customer says it and the check's role is
`agent`. Only the target role or an unknown role can make a violation.

A turn is **relevant** for an absence decision when it is in the scope and its role is the
check's role, or unknown, or the check's role is `any`.

### Quote marks

Quote marks are read from the original turn text. The Persian normalizer turns both `«`
and `»` into `"`, so the normalized text loses the direction of a quote. This package only
detects the marks. It does not fold or normalize text itself.

- `“` and `«` open a quote. `“` is closed by `”`, and `«` by `»`.
- A straight `"` (or the fullwidth `＂`) closes the last open quote when that quote was
  opened by a straight mark. Otherwise it opens a new quote.
- A straight mark cannot show whether it opens or closes. So when a turn has two or more
  pairs of straight marks (4 or more marks), every token between the first and the last
  straight mark is quoted. `"The plan has "guaranteed returns" built in"` is one quote with
  `guaranteed returns` inside it. In `She wrote "hi" and "bye", we offer guaranteed
  returns`, the match is after the last mark, so it stays outside. This rule only adds
  review.
- Quotes can be nested. A stack of open marks gives the depth, so a phrase in an inner
  quote (`«این «سود تضمینی» است»`, `«این "سود تضمینی" است»`,
  `“The plan has "guaranteed returns" built in”`) is still quoted.
- A match is quoted when any of its tokens is inside a quote, at any depth.
- Single quotes are not quote marks here, because English uses `'` in contractions.

The text is cut at each quote mark and each piece is folded alone. Quote marks are
separators for the fold, so the pieces' tokens line up with the turn's tokens.

Fail safe: every match in the turn is quoted when any of these holds:

- a closing mark closes nothing, or closes the wrong mark (for example `”` after `«`, or
  `”` while a straight quote inside it is still open);
- a quote is never closed;
- the pieces' tokens do not line up with the turn's tokens;
- the original text has no quote mark, but its punctuation-keeping form
  `normalize(text, language)` has one.

That gives more review, never a missed doubt. So a stray `"` (for example an inch mark)
sends every match in that turn to review.

### Cue words

Cue words are compared as whole folded tokens, folded with the call's language. The same
list is used for every language, so a mixed Persian and English call is covered.

| Cue | Words |
| --- | --- |
| negation, English | `not`, `no`, `never`, `cannot`, `nothing`, `nobody`, `none`, `neither`, `nor`, `without`, and any `n't` contraction (`can't`, `don't`, `won't` ...) |
| negation, Persian | `نه`, `هرگز`, `نباید`, `نیست`, `نیستم`, `نیستیم`, `نیستید`, `نیستند`, `نبود`, `نبوده`, `ندارم`, `ندارد`, `نداریم`, `ندارید`, `ندارند`, `نکنید`, `نکرد`, `نکردم`, `نکردیم`, `نکرده`, `نخواهد`, `نخواهیم`, `نشد`, `نشده`, `هیچ`, `هیچوقت`, and any token that starts with `نمی` |
| negation, Azerbaijani (basic) | `deyil`, `yox`, `heç` |
| reporting verb, English | `said`, `says`, `told`, `tells`, `saying` |
| reporting verb, Persian | `گفت`, `گفتند`, `گفتید`, `گفتم`, `گفتی`, `گفتیم`, `گفته`, `میگوید`, `میگویند`, `میگفت`, `میگفتند` |
| reporting verb, Azerbaijani (basic) | `dedi`, `dedilər` |

- The fold splits `can't` into `can` and `t`. A token `t` after a token that ends in `n`
  is a negation cue (both tokens) when the turn has an `n't` contraction.
- A cue inside the match itself does not count. Only the tokens before and after it count.
- A reporting verb anywhere in the turn makes every match in that turn quoted speech, even
  a match that contains the verb.
- `نه` also means "nine" in Persian. It still counts as a cue: it only sends a case to
  review.
- More cues only send more cases to review. A new cue is a new version.

## Decision tables

### `required_phrase`, in this order

| Case | outcome | decided_by | review_required | evidence / candidates |
| --- | --- | --- | --- | --- |
| no turns at all | `not_computable`, reason `no_transcript` | null | false | none |
| at least one clean match | `passed` | `rule` | false | evidence = the first clean match (lowest turn index, then lowest token_start) |
| only doubtful matches | undecided (below) | | | candidates = every doubtful match, in order |
| no match, a relevant turn has low ASR confidence | `not_computable`, reason `low_asr_confidence` | null | false | none |
| no match | `failed` | `rule` | false | evidence empty; `scope_turn_indexes` is what was searched |

### `forbidden_phrase`, in this order

| Case | outcome | decided_by | review_required | evidence / candidates |
| --- | --- | --- | --- | --- |
| no turns at all | `not_computable`, reason `no_transcript` | null | false | none |
| at least one clean match | `failed` | `rule` | false | evidence = every clean match, in order |
| only doubtful matches | undecided (below) | | | candidates = every doubtful match |
| no match, a relevant turn has low ASR confidence | `not_computable`, reason `low_asr_confidence` | null | false | none |
| no match | `passed` | `rule` | false | evidence empty |

Rule results are deterministic, so they are confirmed even when `critical` is true. A rule
result has no candidates, even when there are doubtful matches next to the clean ones.

### Undecided (only doubtful matches)

- `model_fallback` false: `not_computable`. The reason is the first doubt of the first
  candidate. `review_required` is true. `review_reasons` is every doubt that occurs among
  the candidates, in the fixed doubt order. No request.
- `model_fallback` true: one `EvaluationRequest`. Its window is the candidate turns plus
  up to 1 turn before and 1 after each, inside the scope, sorted, without duplicates.
  - If the window has more than `MAX_WINDOW_TURNS` turns: no request. The result is as for
    `model_fallback` false, with `window_too_large` added at the end of `review_reasons`.
  - Otherwise `finish_checks` decides it. `met` means the statement was really made:
    `passed` for required, `failed` for forbidden. `not_met`: `failed` for required,
    `passed` for forbidden.

The question of a phrase request is (English, whatever the call language):

> In these turns, did the agent really make the statement "phrase A" or "phrase B" as
> their own words? Answer not_met when the statement is negated, quoted, reported from
> someone else, or said by another speaker.

`the agent` is `the customer` or `any speaker` for those roles. The phrases are the ones
the candidates matched, in definition order.

### `contextual`

| Case | Result |
| --- | --- |
| no turns at all | `not_computable`, `no_transcript` |
| `trigger_phrases` given and none matches a scope turn (any role, doubts ignored) | `not_applicable`, reason `not_triggered`, no request |
| the window has more than `MAX_WINDOW_TURNS` turns | `not_computable`, `window_too_large`, no request |
| otherwise | one `EvaluationRequest`, decided in `finish_checks` |

The window: with triggers, every scope turn with a trigger match, plus up to
`context_turns` turns before and after it, inside the scope. Without triggers, every scope
turn. So a criterion without triggers on a call longer than 12 turns (with the default
whole-call scope) is `window_too_large`. Use a trigger or an opening or closing scope.

`met` gives `passed`, `not_met` gives `failed`.

## Targeted model evaluation

`EvaluationRequest(request_id, check_id, question, language, turn_indexes, turns)`:

- `turns` holds exactly the window turns, each as `(index, speaker_role, text)`, with the
  original text. The model never sees a turn outside the window. That is what "targeted"
  means.
- `request_id` is `<check_id>:<16 hex>`. The hex is the start of a sha256 over the
  canonical JSON of what the model is shown: `CHECKS_VERSION`, the check id, the
  definition digest, the language, the question and the window turns. So the same input
  always gives the same id, and an answer is never reused for another window.
- `request.to_json()` gives plain JSON (each turn as `{index, speaker_role, text}`).

`EvaluationAnswer(verdict, source_turn_indexes=(), confidence=None)`. `verdict` is
`met`, `not_met` or `abstain`. A dict with the same keys is accepted. The constructor
checks nothing. An answer is model output, so `finish_checks` judges it, and a bad answer
is a result, never an exception.

`EvaluatorInfo(provider, model, manifest_id, manifest_version, manifest_status)`.
`manifest_status` is a `ManifestStatus` value of `backend.providers.registry`:
`qualified`, `development` or `retired`. Only `qualified` may decide. The package keeps a
copy of the three values (`MANIFEST_STATUSES`) so it does not import `providers`; a test
keeps the copy equal to the registry. A `ManifestStatus` member is accepted and stored as
its string.

### `finish_checks`, per request, in this order

| Case | outcome | reason |
| --- | --- | --- |
| `evaluator` is `None` | `not_computable` | `evaluator_unavailable` |
| `evaluator.manifest_status` is not `qualified` | `not_computable` | `evaluator_not_qualified` |
| no answer for the request (absent or `None`) | `not_computable` | `evaluator_no_answer` |
| the answer is malformed (below) | `not_computable` | `evaluator_output_invalid` |
| `abstain` | `not_computable` | `evaluator_abstained` |
| confidence `None` or below `MIN_EVALUATOR_CONFIDENCE` | `not_computable` | `evaluator_low_confidence` |
| otherwise | `passed` or `failed`, `decided_by` `model` | null |

An answer is malformed when any of these holds:

- it is not an `EvaluationAnswer` or a dict with only its keys;
- `verdict` is not `met`, `not_met` or `abstain`;
- `source_turn_indexes` is not a list or tuple of ints;
- the verdict is `met` or `not_met` and no turn is cited;
- a cited turn is not in the request's `turn_indexes` (for any verdict, `abstain` too:
  the model cited a turn it never saw);
- `confidence` is not `None` and not a number from 0 to 1 (`NaN` is malformed).

The boundary passes: a confidence of exactly `0.7` is used.

After a request:

- A phrase check that is `not_computable` keeps its doubt as the `reason` and keeps its
  candidates. `review_required` is true. The evaluator code is added at the end of
  `review_reasons`.
- A contextual criterion that is `not_computable` has the evaluator code as its `reason`
  and `review_required` false.
- A model decision has `evidence` = one span per cited turn, sorted by index, with
  `phrase`, `token_start` and `token_end` null. A phrase check keeps its candidates.
- `evaluation` is `{request_id, verdict, confidence, provider, model, manifest_id,
  manifest_version}`. It is set whenever an answer was judged (from the
  `evaluator_output_invalid` row on), and null otherwise. A verdict that is not one of the
  three, or a confidence that is not a finite number, is stored as null.

### Review of a model decision

A model decision needs review when any reason below holds. `review_reasons` lists them in
this order, without duplicates:

1. for a phrase check, the candidates' doubts, in the fixed doubt order;
2. `critical_check`: the check is critical;
3. `speaker_unknown`: a window turn has role `None` and the check's role is not `any`;
4. `low_asr_confidence`: a window turn has low ASR confidence.

Otherwise it is confirmed (`review_required` false).

A phrase check only reaches the model when every match has a doubt. So in version 1 a
phrase check decided by the model always needs review. Only a contextual criterion can be
confirmed by the model alone.

Precision is not measured yet: there is no adjudicated corpus (WP13). So version 1 never
auto-confirms a model decision on a critical check. This is the same choice as
`extraction-policy/1` (HOM-53). F42 asks for precision >= 0.95 **or** review; version 1
takes review.

## Reason codes

`ReasonCode` holds every code of `reason` and `review_reasons`:

| Code | Used as |
| --- | --- |
| `no_transcript` | reason: the call has no turns |
| `not_triggered` | reason of `not_applicable`: no trigger phrase was said |
| `window_too_large` | reason (contextual) or review reason (phrase check) |
| `speaker_unknown`, `wrong_speaker`, `low_asr_confidence`, `quoted_speech`, `negation` | doubts: reason and review reasons; `low_asr_confidence` is also the reason of a blocked absence decision |
| `evaluator_unavailable`, `evaluator_not_qualified`, `evaluator_no_answer`, `evaluator_output_invalid`, `evaluator_abstained`, `evaluator_low_confidence` | the evaluator rows |
| `critical_check` | review reason of a model decision on a critical check |

`Outcome` is `passed`, `failed`, `not_applicable` or `not_computable`. `DecidedBy` is
`rule` or `model`. Both are `str` enums, so they compare equal to their strings.

## Versions and the digest

- `definition_digest(definition)` is sha256 hex over the canonical JSON of the
  definition's JSON dump (`model_dump(mode="json")`, sorted keys, no spaces, UTF-8). It is
  the same canonical form as `taxonomy_digest`. Defaults are part of the dump, so two
  definitions that differ in any field have different digests. A plain dict is validated
  first.
- Every result carries `check_id`, `check_version` and `definition_digest`. The run
  carries `checks_version`.
- A definition change is a new `version` of that check. The owner of the definitions
  (HOM-54 or its config) bumps it.
- A change to any rule, cue word, question template or constant in this package is a new
  `CHECKS_VERSION`. A test pins the version and the constants.
- There is no "latest" helper. A stored result names the versions and the digest that
  produced it. Never judge an old result again with newer rules.

## The result shape

`CheckRun.to_json()`:

```json
{
  "checks_version": "conversation-checks/1",
  "language": "fa",
  "evaluator": null,
  "results": [
    {
      "check_id": "recording_disclosure",
      "check_version": 1,
      "definition_digest": "<64 hex>",
      "kind": "required_phrase",
      "critical": true,
      "outcome": "passed",
      "reason": null,
      "decided_by": "rule",
      "review_required": false,
      "review_reasons": [],
      "evidence": [{"turn_index": 0, "speaker_role": "agent", "start_ms": 0, "end_ms": 4200,
                    "phrase": "this call is recorded", "token_start": 3, "token_end": 7}],
      "candidate_evidence": [],
      "scope_turn_indexes": [0, 1, 2],
      "evaluation": null
    }
  ]
}
```

- `results` has one entry per definition, in definition order.
- `outcome`: `passed`, `failed`, `not_applicable`, `not_computable`.
- `reason`: null for `passed` and `failed`; a code for `not_applicable` and
  `not_computable`.
- `decided_by`: `rule` or `model` for `passed` and `failed`, else null.
- `evaluator` (top level): the `EvaluatorInfo` given to `finish_checks`
  (`{provider, model, manifest_id, manifest_version, manifest_status}`), or null.
- `evaluation`: see "Targeted model evaluation".
- Every list is a JSON array. Enum values are their strings.

## How HOM-54 reads it

| Result | Scorecard |
| --- | --- |
| `passed` or `failed` with `review_required` false | scored |
| anything with `review_required` true | not scored until a person confirms it (HOM-18) |
| `not_applicable` | NA |
| `not_computable` | insufficient evidence |

A `failed` with `review_required` true is never an automatic confirmed violation. A
`not_computable` with `review_required` true goes to review as well; until a person
decides, it is insufficient evidence.

## Not in this version

- No provider adapter. Nothing here calls a model. The wiring ticket sends the requests to
  a qualified evaluator (async) and passes the answers to `finish_checks`.
- No persistence. Nothing is stored. WP11-T06 stores `CheckRun.to_json()`.
- Precision is not measured. Calibration (WP13) may publish a new version that lets a
  measured evaluator confirm critical checks.
- No helper that builds turns from transcript rows.
- No Persian question template. The phrase question is in English.
