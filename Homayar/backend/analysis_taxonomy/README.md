# Conversation taxonomy (`backend.analysis_taxonomy`)

HOM-52 (WP11-T02). This package defines what an analysis may say about a conversation's
**intent**, **topics**, **entities** and **outcome**. It holds:

- a fixed list of supported labels per dimension, in a versioned JSON file;
- the evidence each label needs;
- explicit values for "not said" and for "the model abstains";
- the exact shape of one extraction;
- a pure validator that judges an extraction field by field;
- a pure, versioned policy (HOM-53) that decides what is shown, what is withheld for low
  confidence and what goes to human review.

HOM-53 uses it in the provider prompt, the analysis stage and the call report (see
"How HOM-53 uses it"). HOM-54 builds scorecards on its label ids. This package has no
table, no API and no migration of its own: the stage stores its result in the
`analysis_extractions` table, and the report reads that table.

Import everything from `backend.analysis_taxonomy`.

## The definition file

One file per version: `definitions/{taxonomy_id}.v{version}.json`. Version 1 is
`definitions/conversation.v1.json`.

Top-level keys:

| Key | Meaning |
| --- | --- |
| `schema` | always `homayar.conversation-taxonomy/1` |
| `taxonomy_id` | `conversation` |
| `version` | an integer, `1` |
| `notes` | optional text |
| `dimensions` | exactly `intent`, `topic`, `entity`, `outcome` |

Each dimension has `name_en`, `name_fa`, `definition`, `cardinality` (`single` or
`multi`), `abstain_values`, `text_match` (`none`, or `normalized_token_span` for entity),
`labels` and `retired_label_ids`.

Each label has `id`, `name_en`, `name_fa`, `definition`, `allowed_basis`,
`min_source_turns`, `critical`, `status` (`active` or `deprecated`) and, only on a
deprecated label, an optional `replaced_by`.

The loader (`load_taxonomy`, `load_taxonomy_file`) is strict. It refuses (`TaxonomyError`
with a `code`):

- `taxonomy_unknown`: no file for that id and version, or a bad id. There is no fallback.
- `taxonomy_unreadable`: the file is missing, not UTF-8 or not JSON.
- `taxonomy_invalid`: a wrong `schema`; an unknown or missing key; the same JSON key twice
  in one object; a label id that does not match `^[a-z][a-z0-9_]{1,63}$`; a duplicate id
  in a dimension or across dimensions; a reserved word as an id (`stated`, `not_stated`,
  `insufficient_evidence`, `contradictory`, `explicit`, `inferred`, `unknown`,
  `unavailable`, `other`, `none`); a broken dimension or label rule; content that does not
  match the file name.
- `taxonomy_digest_mismatch`: `expected_digest` was given and is not the file's digest.

## The four dimensions (version 1)

Every label needs at least 1 cited turn.

### intent (single value)

The main reason the customer made contact. If the customer gives more than one reason,
it is the first one they present as the purpose of the contact.
Abstain values: `insufficient_evidence`, `contradictory`.

| id | English | Persian | basis | critical |
| --- | --- | --- | --- | --- |
| `request_support` | Support request | درخواست پشتیبانی | explicit, inferred | no |
| `report_dissatisfaction` | Complaint | ثبت شکایت | explicit, inferred | no |
| `purchase_inquiry` | Purchase inquiry | استعلام خرید | explicit, inferred | no |
| `resolve_billing` | Billing issue | مسئله صورتحساب | explicit, inferred | no |
| `schedule_appointment` | Appointment | تعیین نوبت | explicit, inferred | no |
| `request_callback` | Callback request | درخواست تماس مجدد | explicit, inferred | no |
| `cancel_service` | Cancellation request | درخواست لغو سرویس | explicit | yes |
| `unsolicited` | Unsolicited contact | تماس ناخواسته | explicit, inferred | yes |

### topic (several values)

A subject the conversation discusses in substance, not only in passing.
Abstain value: `insufficient_evidence`. Every topic allows both bases and none is critical.

`pricing`, `refund`, `invoice`, `payment`, `delivery`, `account_access`, `connectivity`,
`contract`, `installation`, `scheduling`, `technical_fault`.

### entity (several values)

A specific item a speaker says, copied exactly from the transcript.
Abstain value: `insufficient_evidence`. Every entity is explicit only.

| id | critical |
| --- | --- |
| `phone`, `national_id`, `email`, `amount`, `reference_id`, `person_name` | yes |
| `date`, `date_range`, `time` | no |

### outcome (single value)

Where the customer's main request stands at the end of the conversation. Use the first
label in this order whose definition fits.
Abstain values: `insufficient_evidence`, `contradictory`.

| id | basis | critical |
| --- | --- | --- |
| `resolved` | explicit | yes |
| `escalated` | explicit | no |
| `follow_up_scheduled` | explicit | no |
| `unresolved` | explicit, inferred | no |

There is no `partially_resolved`: it overlaps with `follow_up_scheduled` and
`unresolved`, so two careful readers would not agree on it.

The JSON file has the full English and Persian names and one-line definitions.

## Status values

| Status | When | `values` |
| --- | --- | --- |
| `stated` | the transcript gives a value and the cited turns support it | 1 or more |
| `not_stated` | the conversation does not touch this dimension | empty |
| `insufficient_evidence` | something may be there, but no turn supports one label well enough | empty |
| `contradictory` | the turns support incompatible labels of a single-valued dimension | empty |

- `stated` and `not_stated`, `explicit` and `inferred` have the same meaning as in HOM-61.
- `insufficient_evidence` and `contradictory` are the **abstain values**. An abstained
  field never carries a label or evidence.
- `insufficient_evidence` is a different word from the evidence status `insufficient`.
- The validator never says `unavailable`. That is the report's word for "no analysis".

## Evidence rules

- A stated value cites at least `min_source_turns` distinct valid turns.
- Its `basis` must be one of the label's `allowed_basis`.
- `critical` labels are high cost when wrong. HOM-53 routes them to review or needs high
  precision for them.
- **Entity text rule.** The text is folded with
  `backend.speech.text_normalize.normalize_for_matching(text, "fa")`: Arabic letter
  variants become Persian, diacritics go, Persian and Arabic-Indic digits become ASCII,
  ZWNJ goes, letters are casefolded, punctuation becomes a space. The folded text must be
  a whole run of tokens of at least one cited turn, folded the same way. So `09123456789`
  matches a turn with `۰۹۱۲۳۴۵۶۷۸۹`, but `4821` does not match `48213`, and
  `09123456789` does not match `0912-345-6789`. The model must copy the text as said.

## The extraction shape

```json
{
  "intent": {"status": "stated", "values": [
    {"label": "resolve_billing", "basis": "explicit", "source_turn_indexes": [0], "confidence": 0.9}
  ]},
  "topic": {"status": "stated", "values": [
    {"label": "refund", "basis": "explicit", "source_turn_indexes": [0, 3], "confidence": null}
  ]},
  "entity": {"status": "stated", "values": [
    {"label": "reference_id", "text": "48213", "basis": "explicit",
     "source_turn_indexes": [2], "confidence": 0.95}
  ]},
  "outcome": {"status": "not_stated", "values": []}
}
```

- The pydantic models are `Extraction`, `LabelField`, `EntityField`, `LabelValue` and
  `EntityValue`. Every key is required. `confidence` may be `null`.
- `source_turn_indexes` means what `IntelligenceResult.source_turn_indexes` means: index
  `i` is turn `i` of the request.
- `confidence` is the model's own number, not a calibrated probability. The validator
  never reads it. Only the policy does.
- At most 32 values per field and 64 indexes per value.
- There is no NPS, no churn probability, no sentiment score and no free-text category.
  Such a key is an unknown key and is rejected.

## The validator

```python
result = validate_extraction(extraction, taxonomy, turns)
```

`extraction` is the decoded JSON object (or an `Extraction`). `turns` are the turn texts
in the order the model saw them. The function is pure and deterministic, and it never
changes its input.

Each field gets a `FieldValidation`: `verdict` (`accepted`, `abstained` or `rejected`),
`status`, the accepted `values`, the `issues` and `rejected_turn_indexes`. The result
also has `taxonomy_id`, `taxonomy_version`, `taxonomy_digest`, `validator_version`,
extraction-level `issues` and `valid`.

| Input | Verdict |
| --- | --- |
| valid `stated` | `accepted` |
| valid `not_stated` | `accepted` |
| valid `insufficient_evidence` or `contradictory` | `abstained` |
| single-valued field with two different labels | `abstained` as `contradictory` |
| anything that breaks a rule | `rejected`, with no values |

Rules run in four tiers per field. Only the first tier with an issue is reported, so one
defect gives exactly one code.

| Tier | Codes |
| --- | --- |
| 1 shape (the pydantic models) | `unknown_key`, `missing_field`, `invalid_value` |
| 2 status and label | `status_not_allowed`, `values_not_allowed`, `missing_values`, `unsupported_label`, `deprecated_label`, `basis_not_allowed` |
| 3 evidence | `turn_index_out_of_range`, `too_few_turns`, `entity_text_not_found` |
| 4 cardinality | `duplicate_label`, `contradictory_values` |

Details:

- A label must match exactly, case-sensitive. `Refund` is not `refund`. Nothing is
  repaired.
- In tier 2, a value with an unsupported label is not checked further.
- In tier 3, a value with a bad turn index is judged on that alone. A value with too few
  turns gets no text check. Bad indexes are counted, never repaired. Duplicate indexes
  collapse to one.
- An unknown top-level key (for example `estimated_nps`) is an extraction issue. The four
  fields are still judged, and `valid` is false.
- For entity, a duplicate is the same label with the same folded text.

## Versions and the digest

- The digest is sha256 over the canonical JSON of the parsed file (sorted keys, no
  spaces, UTF-8), like `manifest_digest`. Formatting does not change it. Any change of
  meaning does.
- A test pins the digest of every released file. Editing a released file fails CI.
- Any change is a new file with the next version. Old files stay forever.
- Add a label: a new version, with an id never used before.
- Change a meaning: not allowed. Add a new id and deprecate the old one.
- Deprecate: keep the label with `status: "deprecated"` (and `replaced_by` if there is
  one). New extractions with it are rejected with `deprecated_label`.
- Remove: only after a released version deprecated it. Leave it out and add its id to
  `retired_label_ids`. A retired id never comes back.
- Evidence rules (`allowed_basis`, `min_source_turns`, `critical`) may change in a new
  version. A `definition` may not.
- `VALIDATOR_VERSION` (`analysis-taxonomy-validator/1`) changes when a validator rule
  changes. A test pins it.
- `load_taxonomy` has no default version and there is no "latest" helper. A caller
  always pins one.

## How HOM-53 uses it

Only two backend modules import this package. A test pins that list.

1. Pin a version as a constant: `load_taxonomy("conversation", 1)`. The provider
   (`backend/providers/openai_compatible.py`) and the analysis stage
   (`backend/services/analysis_extraction.py`) pin the same one. A test checks that.
2. Ask the model for the extraction shape. The OpenAI-compatible provider adds a required
   `extraction` key to its strict output schema (`extraction_schema`). Each label enum
   is the list of active label ids of that dimension, read from the pinned taxonomy.
   The provider passes the value through without judging it.
   `IntelligenceResult.extraction` keeps it exactly as the model gave it, whatever its
   JSON type. A server that does not enforce strict outputs may leave the key out or
   send `null`. The parser then accepts the rest of the output and sets
   `extraction_missing`. A missing extraction never fails the analysis. `None` without
   `extraction_missing` means a legacy result (an old checkpoint, the demo seed, a
   provider that is never asked).
3. The analysis stage (`persist_analysis`, shared by the full job and the recompute)
   calls `evaluate_extraction` with the turns the answering model received. On a
   redacted run those turns hold placeholders, so an entity text must quote the
   placeholder. The stored text is then never the original value.
4. Every kept value (`auto_accepted` or `review_required`, never a withheld one) cites
   its `source_turn_indexes` as evidence spans with `finding_type` `extraction`. Its
   `finding_index` counts the kept values from 0 over intent, topic, entity and outcome,
   and inside a field in the order of `to_json()`.
5. Store `PolicyResult.to_json()` unchanged in `analysis_extractions`, one immutable
   row per analysis run. It carries `taxonomy_id`, `taxonomy_version`,
   `taxonomy_digest`, `validator_version`, `policy_version` and `policy_digest`. The
   row copies them into columns, with `valid`, `needs_review` and `review_count` (the
   kept values that need review, for the HOM-18 review list). A legacy result stores no
   row, and so does a missing extraction (one warning log, with the meeting id only). A
   value that is not an object (an array, a string, a number) is judged invalid and
   stored like any invalid extraction. The table is tenant-only: entity values can be
   personal data, so a share token never reaches a row.
6. The completion audit events (`meeting.processing.completed`,
   `meeting.recompute.completed`) carry `extraction_state` (`unavailable`, `invalid`,
   `review_required` or `accepted`), `extraction_review_count` and `extraction_reason`
   (`missing_from_output`, `not_requested`, or `null` when a row was stored). They never
   carry a label or a text.
7. To read an old result, call
   `load_taxonomy(taxonomy_id, taxonomy_version, expected_digest=taxonomy_digest)`. Never
   judge an old result again with a newer version.
8. The call report (`GET /meetings/{meeting_id}/report`) has one `extraction` block. It
   shows the row of the meeting's current analysis run, never a row of another run.
   `status` is `available` (a valid row), `invalid` (a row with `valid = false`) or
   `unavailable` (no current run, a run that analysed nothing, or no row). The block
   copies the row's ids, versions, `needs_review` and `review_count`. All four fields
   are always there. A field shows its stored `status`, `abstain_reason`,
   `review_required` and its kept values. A rejected field, and every field of an
   `unavailable` block, is `unavailable` with no values. Each value has its `index` (its
   `finding_index`), its label and the label's English and Persian names, read with
   `load_taxonomy(row.taxonomy_id, row.taxonomy_version,
   expected_digest=row.taxonomy_digest)`. If that load fails, the names are `null` and
   the report still answers. An entity value's text is the stored text in the original
   view. In a redacted view every region the text quotes in its cited turns is rendered
   like an evidence reference, and the first rendering that holds a placeholder is
   shown (else the first rendering), so a part of a phone or email shows the whole
   value's placeholder. With no region at all it is the redacted stored text only when
   the run read turns redacted under the same policy as the view
   (`AnalysisRun.input_policy_digest` equals the view's policy digest), else `null`: a
   run redacted with other classes may have quoted a raw part of a value of a class added
   later, which `MeetingView.text` cannot spot. Each value cites the extraction
   spans of its index, with the same evidence status rules as the other report findings.
   Withheld values and stored issues are never shown. The block adds nothing to
   `qa.findings`, the counts, the notices or the review actions, and `report_version`
   stays `1`. Share links and exports never carry it. Only
   `backend/services/analysis_extraction.py` reads the package for the report.

## Confidence and abstention policy

```python
result = apply_policy(validate_extraction(extraction, taxonomy, turns))
# the same thing:
result = evaluate_extraction(extraction, taxonomy, turns)
```

The policy reads only the validator result. It is pure and deterministic, and it never
changes its input. It does no I/O and logs nothing. The analysis stage calls it (see
"How HOM-53 uses it").

### Version 1

`POLICY_V1` is `ExtractionPolicy(version="extraction-policy/1",
min_confidence_explicit=0.5, min_confidence_inferred=0.7)`.

`confidence` is the model's own number, not a calibrated probability. So the version 1
thresholds are conservative placeholders. Precision is not measured yet (there is no
adjudicated corpus), so version 1 never auto-accepts a critical value. Calibration will
publish a new policy version. Human confirmation (accept, override) is HOM-18.

### Per value

These rules apply to each value of a field that the validator accepted as `stated`. They
are the same for single-valued (intent, outcome) and multi-valued (topic, entity) fields.
The threshold is `min_confidence_explicit` for basis `explicit` and
`min_confidence_inferred` for basis `inferred`.

| Value | Result | Disposition or reason |
| --- | --- | --- |
| `confidence` is `null` | withheld | `missing_confidence` |
| `confidence` below the threshold | withheld | `below_threshold` |
| at or above the threshold, `critical` | kept | `review_required`, review reason `critical_label` |
| at or above the threshold, not critical | kept | `auto_accepted` |

- The boundary passes: `0.5` explicit and `0.7` inferred are kept.
- `critical` is the flag the validator copied from the pinned taxonomy. The policy never
  decides criticality by label name.
- A withheld value is never shown as a finding and never goes to review. It stays in the
  result only so evaluation can count it.

### Per field

| Validator result | Policy verdict | Policy status | `abstain_reason` | values / withheld |
| --- | --- | --- | --- | --- |
| `rejected` | `rejected` | `null` | `null` | both empty |
| `abstained`, the input status was an abstain value | `abstained` | the input status | `model` | both empty |
| `abstained` by the validator (`contradictory_values`) | `abstained` | `contradictory` | `contradictory` | both empty |
| `accepted`, `not_stated` | `accepted` | `not_stated` | `null` | both empty |
| `accepted`, `stated`, at least one value kept | `accepted` | `stated` | `null` | kept and withheld, each in input order |
| `accepted`, `stated`, every value withheld | `abstained` | `insufficient_evidence` | `low_confidence` | values empty, withheld in input order |

- A field's `review_required` is true when any kept value is `review_required`.
- `needs_review` is true when any field's `review_required` is true.
- `valid` is the validator's `valid`. The policy never turns an invalid extraction into a
  valid one, nor the reverse.

### Version and digest

- `POLICY_VERSION` is `extraction-policy/1`.
- `policy_digest(policy)` is sha256 over the canonical JSON of the three policy fields
  (sorted keys, no spaces, UTF-8), the same canonical form as `taxonomy_digest`.
- A test pins `POLICY_VERSION` and `policy_digest(POLICY_V1)`. Changing a threshold
  without a new version fails CI.
- `apply_policy` refuses (`ValueError`) a policy with an empty version or a threshold
  outside `[0, 1]`.

### The stored shape

`PolicyResult.to_json()` is plain JSON. Enum values are their strings.

```json
{
  "policy_version": "extraction-policy/1",
  "policy_digest": "<64 hex>",
  "taxonomy_id": "conversation",
  "taxonomy_version": 1,
  "taxonomy_digest": "<64 hex>",
  "validator_version": "analysis-taxonomy-validator/1",
  "valid": true,
  "needs_review": true,
  "issues": [{"code": "unknown_key", "path": "estimated_nps"}],
  "fields": {
    "intent": {"verdict": "accepted", "status": "stated", "abstain_reason": null,
               "review_required": true, "issues": [],
               "values": [{"label": "cancel_service", "basis": "explicit",
                           "source_turn_indexes": [0], "confidence": 0.9,
                           "critical": true, "text": null,
                           "disposition": "review_required",
                           "review_reason": "critical_label"}],
               "withheld": []},
    "topic": {"...": "same keys"},
    "entity": {"...": "same keys; entity values carry their text"},
    "outcome": {"...": "same keys"}
  }
}
```

- `fields` always has exactly the four dimensions.
- A withheld value has the same keys as a kept value, but `disposition` and
  `review_reason` are replaced by `reason` (`below_threshold` or `missing_confidence`).
- An issue has only `code` and `path`. The validator's `message` is for people and is not
  stored. Field `issues` are the validator's field issues.

## How HOM-54 uses it

- A scorecard criterion names a dimension, a label id and the taxonomy version.
- `insufficient_evidence` and `contradictory` map to its insufficient-evidence result.
- `not_stated` maps to NA.
- A rejected field is never scored.

## Mapping to existing ids

`EXTERNAL_ID_MAP` lists the ids that differ. Same ids are not listed.

| Source | Id | Taxonomy |
| --- | --- | --- |
| voicemail `CATEGORY_INTENT` | the 7 intents | same intent ids |
| voicemail `CATEGORY_INTENT` | `unknown` | abstain `insufficient_evidence` |
| voicemail `TOPIC_RULES` | the 10 topics | same topic ids |
| speech entities | the 8 types | same entity ids |
| speech entities | `person_name_slot` | `person_name` |

A test fails when voicemail or speech gains an id that is not here.

## Relation to the report outcome

The report's `outcome.state` (`explicit`, `inferred`, `not_stated`, `unavailable`) says
on what **basis** the report shows an outcome, from its decisions. The taxonomy `outcome`
says what the resolution **is**, with its own basis and turns.

- `stated` with basis `explicit` is the same kind of claim as report `explicit`.
- `stated` with basis `inferred` matches report `inferred`.
- `not_stated` matches report `not_stated`.
- No extraction at all matches report `unavailable`.
- `insufficient_evidence` and `contradictory` have no report word. Never show them as
  `not_stated`.

The two come from different facts and can disagree. The report shows both: its
`outcome.state` from the decisions, and the taxonomy `outcome` field in the
`extraction` block, with `insufficient_evidence` and `contradictory` shown as they are.
The extraction never changes `outcome.state` or its rule.
