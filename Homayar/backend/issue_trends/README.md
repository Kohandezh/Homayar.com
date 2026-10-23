# Trend issues (`backend.issue_trends`)

HOM-79 (WP15-T01, features F49 and F61). This package defines what a trend counts and how.
It holds:

- a versioned list of trend **families** and **issues**, in a JSON file;
- the counting and deduplication rules: what one count means and when two findings are
  one;
- the closed list of supported filters and a pure validator for them;
- the minimum sample thresholds and the cell they produce;
- the shapes HOM-80 and HOM-81 use for aggregates, denominators and coverage.

It is pure. It uses no database, no network and no clock. It reads no file except its own
definition files and the pinned taxonomy file (see "The definition file"). The same input
always gives the same output. It never changes its inputs.

HOM-80 is the first backend user: `services/issue_trend_service.py` and
`GET /api/v1/trends/issues` build the scoped aggregates on these rules (see "HOM-80: the
scoped aggregates"). HOM-81 shows denominators, coverage and missing data. HOM-82 applies
role scope. HOM-83 builds drill-down. HOM-84 reconciles the aggregates with a reference
dataset. This package itself has no table, no API and no migration.

The version of the rules is `issue-trends/1` (`TRENDS_VERSION`). Import everything from
`backend.issue_trends`.

## The definition file

One file per version: `definitions/{definition_id}.v{version}.json`. Version 1 is
`definitions/trends.v1.json`.

Top-level keys:

| Key | Meaning |
| --- | --- |
| `schema` | always `homayar.issue-trends/1` |
| `definition_id` | `trends` |
| `version` | an integer, `1` |
| `notes` | required; text or null |
| `taxonomy` | the pinned taxonomy: `taxonomy_id`, `taxonomy_version`, `taxonomy_digest` |
| `extraction_policies` | the policy versions whose stored rows count: `policy_version`, `policy_digest` |
| `validator_versions` | the validator versions whose stored rows count |
| `families` | the trend families |
| `issues` | the trend issues |
| `retired_issue_ids` | issue ids that are gone and never come back |

A family has `id`, `name_en`, `name_fa`, `definition` (one line), `source`
(`conversation_taxonomy` or `scorecard`), `source_state` (`available` or `unavailable`) and
`source_reason` (`null`, or the reason the source is unavailable).

An issue has `id`, `family`, `name_en`, `name_fa`, `definition` (one line), `source` (its
family's source), `status` (`active` or `deprecated`) and `match`. `match` is a list of 1 to
4 references. Each reference is `{"dimension": ..., "label": ...}`. All references must hold
(AND).

### Why one taxonomy pin for the whole file

A reference names a dimension and a label. Its `taxonomy_id`, `taxonomy_version` and
`taxonomy_digest` are the file's `taxonomy` pin. One file never mixes two taxonomy
versions. So every issue of one trend answer reads the same label meanings. A new taxonomy
version needs a new definition version.

### The loader

`load_definition(definition_id, version, *, expected_digest=None)` and
`load_definition_file(path)`. They are strict. They refuse with a `TrendDefinitionError`
that has a `code`. The checks run in the order below, and the first defect is reported, so
one defect gives exactly one code. `load_definition_file` accepts a file of an unreleased
version when its content matches its name (`trends.v2.json` holding version 2 loads).

| Code | When |
| --- | --- |
| `definition_unknown` | no file for that id and version, a bad id, a bool or a version below 1. There is no fallback. |
| `definition_unreadable` | the file is missing, not UTF-8 or not JSON |
| `definition_duplicate_key` | the same JSON key twice in one object |
| `definition_unknown_key` | a key that is not in the shape above, at any level |
| `definition_bad_id` | a family or issue id that does not match `^[a-z][a-z0-9_]{1,63}$`, or a reserved word (`RESERVED_IDS`: the taxonomy's reserved words plus `counted`, `pending_review`, `absent`, `missing`, `all`, `total`) |
| `definition_invalid` | a wrong `schema`; a missing key; a wrong type; a broken family or issue rule (below); content that does not match the file name |
| `definition_duplicate_id` | a family or issue id twice (families and issues share one id space), or an issue id that is in `retired_issue_ids` |
| `reference_taxonomy_unknown` | the pinned taxonomy cannot be loaded |
| `reference_taxonomy_digest_mismatch` | the pinned digest is not the taxonomy file's digest |
| `reference_version_mismatch` | an `extraction_policies` or `validator_versions` entry this package does not know |
| `reference_inactive_label` | the label is deprecated |
| `reference_retired_label` | the label id is in the dimension's `retired_label_ids` |
| `reference_wrong_dimension` | the label exists, but in another dimension |
| `reference_unknown_label` | the label is in no dimension of the pinned taxonomy |
| `definition_digest_mismatch` | `expected_digest` was given and is not the digest of a file that passed every check above |

Family and issue rules (`definition_invalid`):

- An `available` family has at least one active issue and `source_reason: null`.
- An `unavailable` family has no issue and a `source_reason`.
- An issue names a family of the same file and has that family's `source`.
- A `conversation_taxonomy` issue has 1 to 4 references. A reference dimension is
  `intent`, `topic` or `outcome`. At most one reference per single-valued dimension
  (intent, outcome). No reference twice in one issue.
- No two active issues have the same set of references. So one thing is never counted
  under two issue ids.
- `extraction_policies` and `validator_versions` are not empty.

Entity is never a reference dimension. An entity value can be personal data (a phone, a
national id). It is never a trend issue and never a join key.

**The only file read outside the package.** To check references, the loader reads the
pinned taxonomy through `backend.analysis_taxonomy.load_taxonomy(taxonomy_id,
taxonomy_version, expected_digest=taxonomy_digest)`. That is the only file it reads outside
its own `definitions/` folder. It reads no other file.

Only one module of this package imports `backend.analysis_taxonomy`:
`backend/issue_trends/definition.py` (`load_taxonomy`, `TaxonomyError`, `POLICY_V1`,
`POLICY_VERSION`, `policy_digest`, `VALIDATOR_VERSION`). The counting, filter, sample and row
modules never import it. `tests/test_analysis_taxonomy_mapping.py` pins the list of
importers, so turn 2 adds `issue_trends/definition.py` to that list and to the sentence
"Only two backend modules import this package" of `analysis_taxonomy/README.md` (SPEC
section 5 allows both).

The policy and validator pins are checked against the code: version 1 knows
`extraction-policy/1` with `policy_digest(POLICY_V1)`, and `analysis-taxonomy-validator/1`.

## Families and issues (version 1)

A **trend issue** is one named thing a trend counts. Each issue belongs to one **family**.
Four families cover the four aggregates of HOM-80.

| Family | Source | State | Issues |
| --- | --- | --- | --- |
| `call_reason` (Call reasons, دلایل تماس) | conversation taxonomy | available | 8 |
| `unresolved_outcome` (Unresolved outcomes, نتیجه‌های حل‌نشده) | conversation taxonomy | available | 3 |
| `service_complaint` (Product and service complaints, شکایت از محصول یا خدمت) | conversation taxonomy | available | 11 |
| `qa` (Quality checks, کنترل کیفیت) | scorecard | unavailable (`scorecard_results_unavailable`) | 0 |

Issue ids follow one pattern, so a reader can see the reference in the id:
`{dimension}_{label}` for one reference, and `complaint_{topic}` for a complaint. The
loader does not enforce the pattern. It enforces that no two issues share a reference set.

### call_reason

One issue per active intent label. Intent is single-valued, so the eight issues never
overlap. Their sum is the family count.

| Issue | Reference |
| --- | --- |
| `intent_request_support` | intent `request_support` |
| `intent_report_dissatisfaction` | intent `report_dissatisfaction` |
| `intent_purchase_inquiry` | intent `purchase_inquiry` |
| `intent_resolve_billing` | intent `resolve_billing` |
| `intent_schedule_appointment` | intent `schedule_appointment` |
| `intent_request_callback` | intent `request_callback` |
| `intent_cancel_service` | intent `cancel_service` (critical) |
| `intent_unsolicited` | intent `unsolicited` (critical) |

Example: a call with intent `resolve_billing` auto-accepted counts once for
`intent_resolve_billing` and once for the family `call_reason`.

`cancel_service` and `unsolicited` are critical labels. Extraction policy 1 never
auto-accepts a critical value, so in version 1 these two issues are always
`pending_review`, never confirmed. This is the honest state. A call whose intent is a
pending `cancel_service` is `absent` for the other seven call reasons, but that absence
rests on an unconfirmed value, so it is also counted in `absent_pending_review` (see
"Absent on an unconfirmed value").

Assumption, not built yet: HOM-18 lets a person confirm or override a value. Nothing here
depends on how HOM-18 does it. A reviewed value would need a new extraction policy or a
new rule version to count as confirmed.

### unresolved_outcome

The main request was not fully handled during the conversation.

| Issue | Reference |
| --- | --- |
| `outcome_unresolved` | outcome `unresolved` |
| `outcome_escalated` | outcome `escalated` |
| `outcome_follow_up_scheduled` | outcome `follow_up_scheduled` |

Outcome is single-valued, so these three never overlap. `resolved` is not an issue: it is
the opposite of this family. A call with outcome `resolved` is assessable and `absent` for
all three.

Example: outcome `escalated` auto-accepted. `outcome_escalated` is counted.
`outcome_unresolved` and `outcome_follow_up_scheduled` are absent. The family counts it
once.

**Read this denominator with care.** `resolved` is a critical label, so extraction policy 1
puts every `resolved` value in review. A call with outcome `resolved` is `absent` for all
three issues, but only because a value that waits for review says so. In version 1, most of
the `unresolved_outcome` denominator rests on pending `resolved` values. The row's
`absent_pending_review` count shows exactly how many, so HOM-81 can show it next to the
rate.

### service_complaint (product and service)

**There is no product or service field in the data model.** `Meeting` has no product,
and there is no catalogue. So version 1 expresses a product or service complaint as:

> intent `report_dissatisfaction` **and** topic X, in the same conversation.

The topic is the **service area**. There is one issue per active topic label:
`complaint_pricing`, `complaint_refund`, `complaint_invoice`, `complaint_payment`,
`complaint_delivery`, `complaint_account_access`, `complaint_connectivity`,
`complaint_contract`, `complaint_installation`, `complaint_scheduling`,
`complaint_technical_fault`.

Example: intent `report_dissatisfaction`, topics `refund` and `invoice`, all
auto-accepted. `complaint_refund` and `complaint_invoice` are each counted once. The family
`service_complaint` counts the conversation **once**, not twice.

What this does **not** support:

- a product or a service by name, SKU, plan or catalogue id;
- a complaint whose topic is not stated (it is counted by `intent_report_dissatisfaction`
  of `call_reason`, but by no `service_complaint` issue);
- a complaint that is not the main intent (a call about billing that also complains is a
  `resolve_billing` call). The taxonomy has one intent per call;
- `cancel_service` is a call reason, not a complaint.

Why a topic and not a new label: the taxonomy already has the topics. A parallel
"product" label would be a second word for the same thing.

### qa

There are no scorecards on main (HOM-54 is not merged). Conversation check results
(HOM-55) and speech metrics (HOM-51) are not stored anywhere. So the family exists, with
`source_state: unavailable` and `source_reason: scorecard_results_unavailable`, and no
issues. Its cell is always `not_computable` with that reason. HOM-80/81 show "source not
available", never a zero.

Why not the report's `qa.findings`: those findings are the AI summary, insights, decisions
and action items, each with its evidence status (`routers/v1/reports.py`). They measure
whether the **analysis** is backed by evidence, not whether the **call** met a quality
rule. Counting them as QA would label an analysis gap as an agent's quality problem. The
evidence gap is already visible in `coverage/1` as the evaluation bucket
`unsupported_findings`.

A later definition version makes `qa` available with real issues, one per stored criterion,
once scorecard results are stored.

### Not supported in version 1

- **Operational issues from speech metrics** (long silence, overlap, talk ratio). Speech
  metrics are not stored, and there is no measured basis for a threshold. A threshold
  without a reason would be invented. So there is no operational family.
- **Repeat contact** (F50). It needs a CRM-linked customer identity, which does not exist.
- **Entity issues.** An entity is personal data, never a trend.
- **Sentiment, NPS, churn.** The taxonomy has none.

## Counting and deduplication

### The counting unit

**One count is one conversation (one meeting).** A conversation adds at most 1 to an issue,
and at most 1 to a family. Every number of a trend is a number of distinct conversations.

Why: the denominators of `coverage/1` are conversations too, so a rate of an issue and a
coverage ratio speak about the same thing.

### The population

The conversations a trend can count are the ones of the `coverage/1` processing bucket
`transcribed` in the window: an accepted ledger row (`ingestion_records`) with
`from <= received_at < to`, a live meeting (`deleted_at` is null) in status `completed` or
`partial`, and a current transcript revision with at least one segment. A meeting without a
ledger row (legacy or demo data) is not counted, like in coverage. HOM-80 selects this
population in SQL, then applies the population filters and the caller's scope.

### When two findings count as one

| Rule | Example |
| --- | --- |
| Inside one conversation an issue counts at most once, however many values, turns or fields support it. | intent `resolve_billing` cites turns 0, 3 and 7: `intent_resolve_billing` counts 1. |
| A multi-valued dimension counts once per issue. | topics `refund` and `invoice` with a complaint: `complaint_refund` 1, `complaint_invoice` 1. |
| A family counts distinct conversations, never the sum of its issues. | the same call: `service_complaint` counts 1, not 2. |
| Only the meeting's **current** analysis run counts (the report's `extraction` rule). | a meeting analysed three times: only the row of the run with `is_current` is read. The other two rows are never read. |
| Two conversations are never merged into one issue in version 1. | two calls from the same phone number about the same refund count 2. There is no customer identity, and an entity value is never a join key (blueprint F64: no phone-number-only identity merge). |
| A replay of the same ingestion creates nothing. | the same event id and payload sent twice: one ledger row, one meeting, counted once (case A of `ingestion_service.py`). A conflict (case B) also creates nothing. |
| Copies of the same media form one media group, and only its counted copy can count. | see "Duplicate media" below. |

### Duplicate media

With workspace policy `allow`, a new event whose media matches a live earlier ingestion is
accepted and linked to it with `duplicate_of_id` (case C of `ingestion_service.py`). The
link points to the earliest accepted ingestion of that media whose conversation is not
deleted (`find_live_duplicate`, `ingestion_service.py:208-223`).

**Media group.** A media group is one original ingestion plus every ingestion that links
to it. The group key (`media_group_id`) is the **root of the `duplicate_of_id` chain**: follow
`duplicate_of_id` until a row that has none. A row with no `duplicate_of_id` is its own root.

Why the root and not just `duplicate_of_id`: a duplicate can point to another duplicate.
Example: B links to A. A's conversation is deleted. C arrives: the earliest live copy is now
B, so C links to B. Keyed by `duplicate_of_id`, B (key A) and C (key B) would be two groups
and could both count. By the root, A, B and C are one group. A chain never loops: a link
always points to a row that already existed, and acceptance is serialized per workspace
(a row lock on the workspace, `ingestion_service.py:339`). HOM-80 resolves the root with a
recursive query.

Two groups with the same media can exist: a new root is created only when no copy of that
media was live (`find_live_duplicate` found none). Then every member of the older group has
a deleted conversation. No code sets `deleted_at` back to null, so the older group can never
again have a transcribed member. So at most one group of a media can count, and grouping by
the chain root counts the same as grouping by `media_hash`. A row without a `media_hash`
(a backfilled row) is never linked and is its own group.

**The counted copy.** In each group, the counted copy is the earliest member in the coverage
`transcribed` state (accepted row, live meeting, status `completed` or `partial`, a current
revision with at least one segment), ordered by `received_at`, then by ingestion id (string
order) as a tie-break. Every other transcribed member is `excluded.duplicate_media`. A group
with no transcribed member counts nothing (none of its members is in the population).
`counted_copy_id(members)` is the reference rule. The facts are
`ConversationFacts.media_group_id` and `ConversationFacts.is_counted_copy`.

The counted copy is decided over the whole group, whatever the window, the filters and the
caller's scope. **Then the window, the filters and the scope apply to the counted copy
only.** A call belongs to the window, team and scope of its counted copy. So:

| Case | What happens |
| --- | --- |
| The earliest copy failed (or is in progress, or has no transcript); a later copy is transcribed. | The later copy is the counted copy. It counts as a normal conversation. |
| The earliest copy was never transcribed; two later copies are transcribed. | Only the earlier of the two (by `received_at`, then id) counts. The other is excluded. |
| The earliest copy is deleted. | It is not transcribed. The next transcribed member is the counted copy. |
| The counted copy is in the same answer. | The other members are excluded. The call counts once, as the counted copy. |
| The counted copy was received outside the window. | The members in this window are excluded. The call is not counted in this answer: it belongs to the window of the counted copy. |
| The counted copy is outside a population filter (for example another team). | The members in this answer are excluded. The call is not counted in this answer. |
| The counted copy is outside the caller's scope. | The members in this answer are excluded. The call is not counted in this answer. |

So a call is counted **at most once**, and only in answers whose window, filters and scope
include its counted copy. `tally` refuses (`ValueError`) two included judgments with the same
`media_group_id`.

Why: the same audio is the same call. Counting it twice would double an issue. Why the
counted copy decides and not the answer: then the result never depends on which copy the
window or a filter happens to include. The counted copy can change when a member's
transcribed state changes (an earlier copy finishes, or the counted copy is deleted); the
call then moves to that copy's window, team and scope. Why an
exclusion and not a missing-data bucket: nothing is missing about the call. The exclusion
count stays visible so the population still reconciles with coverage (see "The coverage
link").

The counted copy can change later: if it is deleted, the next transcribed member becomes the
counted copy. An answer for an old window can then change, like it does when any
conversation is deleted.

For HOM-80: the counted copy needs the group's members outside the caller's scope, the
window and the filters. The query reads them only to decide the exclusion. It never returns
them, and an answer shows only its own `excluded.duplicate_media` count.

For HOM-82: when the counted copy is outside the caller's scope, the in-scope member shows
up as `excluded.duplicate_media`. That reveals that a copy exists elsewhere. It is a count
only, never content.

### The steps per conversation

`judge_conversation(facts, definition)` runs these steps in order. The first step that
applies decides for the whole conversation.

1. `is_counted_copy` is false: **excluded** (`duplicate_media`).
2. No current run, or its state is `skipped`, `unavailable` or `failed`:
   `analysis_unavailable`.
3. The current run read another transcript revision than the meeting's current one
   (`analysis_revision_id != current_revision_id`): `analysis_stale`. This is the word and
   the rule of the report and coverage (`services/quality_service.py:209`). Why: the
   extraction describes a transcript that is no longer the one shown.
4. The current transcript revision is a mock, or the current run is a mock
   (`transcript_is_mock or analysis_is_mock`): `mock_analysis`. This is the coverage rule
   (`quality_service.py:227-229`). Why: the mock provider states every dimension
   `not_stated` (`providers/mocks.py:156-160`). Counted, it would make every issue look
   absent.
5. The current run stored no row: `extraction_unavailable`.
6. The six version fields cannot be read (see "The stored shape"): `extraction_unreadable`
   with reason `shape_version_fields`.
7. The row's taxonomy id, version and digest are not the definition's pin, or its
   `policy_version`/`policy_digest` pair is not in `extraction_policies`, or its
   `validator_version` is not in `validator_versions`: `version_mismatch`. Why: a new
   taxonomy or policy changes what a label or a disposition means. A trend never mixes them
   silently. Why before the shape: a row of a future version may have another shape. It is
   a version mismatch, not a broken row.
8. The rest of the row is not the stored shape: `extraction_unreadable` with the first
   defect's reason.
9. The row has `valid: false`: `extraction_invalid`.
10. Otherwise each dimension and each issue is judged as below.

Steps 2 to 9 give the same bucket to every dimension, issue and family of the conversation.

A current run in state `queued`, `running`, `partial` or `insufficient_evidence` analysed
something, so it is judged on its row; with no row it is `extraction_unavailable`.

### The stored shape

The judge reads `analysis_extractions.result`: the stored `PolicyResult.to_json()` of
`extraction-policy/1` (`analysis_taxonomy/policy.py:143-156`, `:284-310`). The constants
are in `backend.issue_trends` (`VERSION_FIELDS`, `RESULT_KEYS`, `FIELD_NAMES`,
`FIELD_KEYS`, `ISSUE_KEYS`, `VALUE_KEYS`, `WITHHELD_KEYS` and the word sets).

**Version fields first.** The result must be a JSON object, and these six keys must be
there with these types: `taxonomy_id` string, `taxonomy_version` integer (not a bool),
`taxonomy_digest` string, `policy_version` string, `policy_digest` string,
`validator_version` string. Nothing else is read for steps 6 and 7. A defect here is
`shape_version_fields`.

**Then the shape.** Every object must have exactly its keys. A missing key and an extra key
are both refused. Why refuse an extra key: `to_json()` writes exactly these keys and a row
never changes. An extra key means a shape this version does not know, and guessing is not
safe.

| Object | Keys and types |
| --- | --- |
| result | the six version fields; `valid` bool; `needs_review` bool; `issues` list of issue objects; `fields` object |
| `fields` | exactly `intent`, `topic`, `entity`, `outcome`, each a field object |
| field | `verdict` word; `status` word or null; `abstain_reason` word or null; `review_required` bool; `issues` list of issue objects; `values` list of value objects; `withheld` list of withheld objects |
| issue | `code` string; `path` string |
| value | `label` string; `basis` word; `source_turn_indexes` list of integers; `confidence` number or null; `critical` bool; `text` string or null; `disposition` word; `review_reason` word or null |
| withheld | the value keys without `disposition` and `review_reason`, plus `reason` word |

Allowed words: `verdict` `accepted`, `abstained`, `rejected`. `status` `stated`,
`not_stated`, `insufficient_evidence`, `contradictory`. `abstain_reason` `model`,
`contradictory`, `low_confidence`. `basis` `explicit`, `inferred`. `disposition`
`auto_accepted`, `review_required`. `review_reason` `critical_label`. `reason`
`below_threshold`, `missing_confidence`. A bool is never an integer or a number.

`entity` is checked like the other three fields, for types and words only. Its labels and
texts are never compared or kept. Why check it at all: one rule for the four fields, and a
broken entity field means the row is broken.

Labels are not checked against the taxonomy. The row was judged once; it is never judged
again (the taxonomy README's rule).

**Inconsistent pairs** (`shape_inconsistent`), checked only when the types and words are
right. These are pairs the policy never writes:

- `rejected` with a `status`, an `abstain_reason`, a value, a withheld value or
  `review_required: true`;
- `abstained` with a status that is not an abstain value, with no `abstain_reason`, or with
  a kept value; `abstain_reason: contradictory` with a status other than `contradictory`;
  `low_confidence` with a status other than `insufficient_evidence` or with no withheld
  value; `model` or `contradictory` with a withheld value;
- `accepted` with an abstain status, with `status: null` or with an `abstain_reason`;
  `accepted` `not_stated` with a value or a withheld value; `accepted` `stated` with no
  kept value;
- an `intent` or `outcome` field (single-valued) with more than one entry in `values` and
  `withheld` together;
- a field whose `review_required` is not "some kept value is `review_required`";
- a value with `review_required` and no `review_reason: critical_label`, or with
  `auto_accepted` and a `review_reason`;
- `needs_review` that is not "some field has `review_required`";
- `valid` that is not "no top-level issue and no `rejected` field" (`validator.py:124-126`).
  So `valid: true` with a `rejected` field is unreadable.

**One defect, one reason.** The walk is fixed: the result object, then its top-level
`issues` items in list order, then `fields`, then the fields in the order `intent`,
`topic`, `entity`, `outcome`, inside a field its `issues`, `values` and `withheld` items in
list order. At each object: `shape_missing_key`, then
`shape_unknown_key`, then `shape_bad_type`, then `shape_bad_word`. The consistency checks
run after the whole walk passes: the fields in the same order, then `needs_review`, then
`valid`. The first defect is the `unreadable` reason of the judgment.

A row with `valid: false` is never partly used. The validator sets `valid` false when any
field is rejected or the extraction has an issue of its own (`validator.py:125`). So a
rejected field always means `extraction_invalid` for the whole conversation. Why: the model
broke the output contract, so its other fields are not trusted for counts. It also keeps the
SQL simple (the `valid` column).

The facts must be honest about the run. `judge_conversation` raises `ValueError` for a row of
another run (`extraction_run_id` is not `analysis_run_id`), a row without a run, or a run
without a state.

### Status mapping

**Per dimension** of a valid row (the stored field):

| Stored field | Dimension result |
| --- | --- |
| `accepted`, `stated` | assessed |
| `accepted`, `not_stated` | assessed |
| `abstained`, `abstain_reason: model`, status `insufficient_evidence` | `abstained_insufficient_evidence` |
| `abstained`, `abstain_reason: model`, status `contradictory` | `abstained_contradictory` |
| `abstained`, `abstain_reason: contradictory` (the validator found it) | `abstained_contradictory_values` |
| `abstained`, `abstain_reason: low_confidence` (every value withheld) | `abstained_low_confidence` |

`abstained` and `contradictory` are **never** `not_stated`. `not_stated` means "the call does
not touch it": the issue is absent. An abstain means "we could not tell": missing data.

**Per reference** (dimension `d`, label `l`):

| Stored values of `d` | Reference result |
| --- | --- |
| `d` is abstained (any abstain row of the table above) | missing, with that dimension's bucket |
| a kept value `l` with `auto_accepted` | holds, confirmed |
| a kept value `l` with `review_required` (critical label) | holds, pending review |
| `l` only among `withheld` (`below_threshold`, `missing_confidence`) | missing, `value_withheld` |
| no value `l` at all (or status `not_stated`) | does not hold |

If `l` is there more than once, the best wins: auto-accepted, then review, then withheld.

**Per issue**, combining its references (AND):

1. any reference does not hold: `absent`;
2. else any reference is missing: `missing`, with the bucket of the first missing
   reference in `match` order;
3. else any reference is pending review: `pending_review`;
4. else `counted`.

Example: `complaint_refund` on a call with intent `request_support` and topic `refund`
withheld. The intent reference does not hold, so the issue is `absent`. The withheld topic
does not matter: a complaint is not there.

Example: intent `report_dissatisfaction` auto-accepted, topic field abstained with
`low_confidence`. `complaint_refund` is `missing` (`abstained_low_confidence`).

**Per family** (OR over its active issues):

1. any issue `counted`: `counted`;
2. else any `pending_review`: `pending_review`;
3. else any `missing`: `missing`, with the first bucket in `MissingBucket` order;
4. else `absent`.

Example: `unresolved_outcome` with outcome `escalated` pending review (not possible in
version 1, but the rule is general): family `pending_review`. With outcome `resolved`: all
three issues absent, family absent.

### Absent on an unconfirmed value

An issue is `absent` when one of its references does not hold. On a single-valued dimension
(intent, outcome), a reference does not hold when **another** label is the kept value. If
that other value is `review_required`, the absence rests on a value no person has
confirmed yet.

So a judged issue carries `absent_pending_review`: true when the issue is `absent` and every
reference that does not hold is on a single-valued dimension whose kept value is
`review_required`. A reference on a multi-valued dimension (topic) that does not hold is
always a firm absence: another topic never excludes this one.

A family is `absent_pending_review` when it is `absent` and one of its issues is.

Example: outcome `resolved` with `review_required`. `outcome_unresolved` is `absent`, and
`absent_pending_review` is true. With outcome `resolved` auto-accepted (not possible in
policy 1) it would be false.

Example: intent `cancel_service` pending, topic `refund` auto-accepted. `complaint_refund` is
`absent` with `absent_pending_review` true (the intent reference rests on the pending
`cancel_service`).

`absent_pending_review` is a part of `absent`. It is never added to the denominator a second
time. HOM-81 shows it next to the rate, so a reader knows how much of the denominator is not
confirmed.

**The four places a conversation can land**, for one issue:

| Place | In the numerator | In the denominator |
| --- | --- | --- |
| `counted` (confirmed) | yes | yes |
| `pending_review` | no, counted apart as `pending_review` | yes |
| `absent` (assessable without the issue) | no | yes |
| `missing` (one bucket) | no | no, counted per bucket |

Why pending review is in the denominator: it is assessable. Only its confirmation waits. So
when a person confirms or rejects it, the denominator does not move and only the numerator
can change. A pending value is never merged into the confirmed number.

### The tally

`tally(judgments, definition)` adds conversations up. Per issue and per family it gives
`confirmed`, `pending_review`, `absent`, `absent_pending_review` (a part of `absent`) and
`missing` (every bucket key, 0 when empty). For every issue and family:

`confirmed + pending_review + absent + sum(missing) == included`.

`tally` raises `ValueError` for the same meeting id twice. Why: a SQL join that repeats a
meeting must fail loudly, never count it twice.

### What HOM-80 reads from the database

**The JSON paths.** `analysis_extractions.result` is a `JSON` column, not `JSONB`
(`migrations/versions/c7e2a9d4f1b6_analysis_extractions.py:59`), so it has no JSON index.
The rules read: the six version fields at the top level; `valid`; for each of `intent`,
`topic`, `outcome`: `fields.<name>.verdict`, `.status`, `.abstain_reason`,
`.values[*].label`, `.values[*].disposition` and `.withheld[*].label`. The shape check also
walks every key of the result, `entity` included. The columns `valid`, `taxonomy_*`,
`policy_*` and `validator_version` copy the top-level values, so SQL can filter on them
without the JSON. HOM-80 measures the cost of reading the JSON in SQL against loading the
rows and judging them in Python, and picks one.

**Enum columns store the member name.** The project's non-native enums store the member
name, not the value (`database/saas_models.py:246-247`). So `analysis_runs.state` holds
`COMPLETED`, and `ingestion_records.source_type` holds `FILE_UPLOAD`. The ORM gives back
the enum member. HOM-80 passes `StageState(...).value` (lowercase, one of `STAGE_STATES`) as
`ConversationFacts.analysis_state`. A raw SQL query maps a name with
`StageState[name].value`. The `source_type` filter values are enum values too: in raw SQL,
HOM-80 compares with `IngestionSourceType(value).name`.

**SQL returns integer counts only.** Only this package computes a ratio or a difference
(see "Rounding"). SQL never divides.

## Supported filters

`validate_filters(raw, definition, *, now)` checks decoded query parameters and returns a
`TrendFilters`. `now` is the caller's clock (the package reads none). `now` is checked
first, always, before any filter check: a `now` without a timezone raises `ValueError`, not
a `TrendFilterError`, even when `to` is given. Why: `now` comes from the server
code, never from the user, so a naive `now` is a programming error.

There are two kinds of filter. A **population filter** narrows the conversations. A
**selector** chooses which rows are returned. A selector never changes a row's numbers.

| Key | Kind | Type | Allowed values | Maps to |
| --- | --- | --- | --- | --- |
| `from` | population | one string | ISO 8601 with a timezone | `IngestionRecord.received_at >= from` |
| `to` | population | one string | ISO 8601 with a timezone | `IngestionRecord.received_at < to` |
| `team_id` | population | string or list of strings | canonical lowercase UUID text | `Meeting.team_id IN (...)` |
| `agent_user_id` | population | string or list of strings | canonical lowercase UUID text | `Meeting.agent_user_id IN (...)` |
| `language` | population | string or list of strings | `^[a-z-]{2,12}$` after lowercasing | `Meeting.language IN (...)` |
| `source_type` | population | string or list of strings | an `IngestionSourceType` value (`file_upload`, `microphone`, `system_audio`, `mic_plus_system_audio`, `sip_voip`, `meeting_bot`, `external_recording_url`, `offline_folder_watcher`, `api`, `voicemail`) | `IngestionRecord.source_type IN (...)` |
| `family` | selector | string or list of strings | a family id of the definition | family rows, and issues of these families |
| `issue` | selector | string or list of strings | an active issue id of the definition | these issue rows |
| `service_area` | selector | string or list of strings | an active topic label id of the pinned taxonomy | issues with a reference to that topic |

Rules:

- **The window** uses the `coverage/1` rules, basis `ingestion_received_at`. Default: `to` is
  `now`, `from` is 30 days before `to`. Refused: a value without a timezone, a value that is
  not ISO 8601, `from >= to`, a span over 366 days, a date that cannot be shifted to UTC.
  Why the same rules: HOM-81 shows the coverage counters of the same window next to a trend.
  The two must cover the same conversations. Why `received_at` and not a call time: there is
  no stored call start time, and coverage counts by `received_at`.
- A single string is a one-item list. Lists are sorted and duplicates collapse. At most 50
  distinct values per key. An empty list or an empty string is a bad value.
- `language` is lowercased, like the upload does. Nothing else is repaired.
- Selectors combine by AND. `family=call_reason&issue=complaint_refund` selects nothing.
  That is an empty answer, not an error.
- Family rows are returned only when there is no `issue` and no `service_area`. Why: a family
  row is always the whole family. A narrowed "family" number would mean something else.
- **Product.** Product is a topic-based selector: `service_area`. Its allowed values are
  `service_area_ids(definition)`, the active topic labels of the pinned taxonomy. The key
  `product` is refused (see below).
- **No team, no agent.** `Meeting.team_id` and `Meeting.agent_user_id` can be null. Such a
  meeting appears only in answers without a `team_id` (or `agent_user_id`) filter. So
  per-team rows need not add up to the workspace total: the difference is the meetings
  with no team. Version 1 has no "no team" value.

**Time series.** A series is a list of points. Each point is its own window `[from, to)`,
checked with the same rules, and judged and tallied on its own. Boundaries are instants
with a timezone, so a cut at midnight in any timezone is exact. HOM-80 chooses the grain
(day, week, month) and the timezone of the cuts. Each point obeys the 366-day limit; the
range of a whole series is HOM-80's choice. A call is in the point whose window holds its
`received_at` (a duplicate: the window of its counted copy).

Errors (`TrendFilterError` with `code` and `key`). The checks run in this order, and the first
defect is raised:

1. key checks, one class after the other, each class over its keys in sorted order:
   - first `filter_scope_not_allowed`, for any scope key: `workspace_id`,
     `organization_id`, `role`, `scope`;
   - then `filter_unsupported`, for any known word version 1 does not support: `product`,
     `product_id`, `service`, `customer_id`, `phone`, `entity`, `repeat_contact`,
     `scorecard`, `sentiment`;
   - then `filter_unknown_key`, for any other key that is not in the table.

   The more serious refusal wins. Example: `{"aaa": "x", "workspace_id": "y"}` gives
   `filter_scope_not_allowed` with key `workspace_id`, although `aaa` sorts first. Why: a
   request that tries to set its own scope must be told so, not "unknown key".
2. `filter_bad_type`, over the keys in sorted order: not a string or a list of strings, or
   a list for `from` or `to`;
3. `filter_bad_value`, over the keys in sorted order: a value outside its allowed values,
   too many values, an empty value;
4. `filter_window_invalid`: any window rule above (for `from`, `to` or both). Its `key` is
   the end at fault, or `null` when the two ends together break a rule (`from >= to`, a
   span over 366 days).

Why `filter_window_invalid` and not the coverage word `coverage_window_invalid`: every
filter error starts with `filter_`, so a client tells all of them apart by one prefix. The
rules are the coverage rules; only the word differs. HOM-80 maps every filter code to a 422,
like coverage does.

**Scope is not a filter.** Workspace, role and team membership come from the caller's
authorization. HOM-80 and HOM-82 apply them and always combine them with the filters by
AND. So a filter can only narrow what the caller may already see. A `team_id` outside the
caller's scope never widens the answer. HOM-82 decides whether it is a 403 or an empty
answer.

## Minimum sample thresholds

`sample_cell(numerator, denominator, *, kind, policy)` gives one cell. It uses the counter
words of `coverage/1`, with one new status.

| Case | `status` | `numerator` | `ratio` | `reason` |
| --- | --- | --- | --- | --- |
| `denominator == 0` | `not_computable` | `0` | `null` | `empty_denominator` |
| `0 < denominator < minimum` | `insufficient_sample` | the count (rate) or `null` (agent) | `null` | `below_minimum_sample` |
| `denominator >= minimum` | `computed` | the count | `n / d`, rounded (see "Rounding") | `null` |
| source unavailable (`unavailable_cell`) | `not_computable` | `null` | `null` | `scorecard_results_unavailable` |

- The boundary passes: `denominator == minimum` is `computed`.
- An empty denominator is always `empty_denominator`, never `insufficient_sample`.
- A missing source wins over `empty_denominator`, like the coverage review counter.
- `reason` is `null` exactly when the cell is computed.
- Every cell carries `minimum_sample`, so a reader sees "12 of the 30 needed".
- `ValueError` for a bool, a non-integer, a negative number or `numerator > denominator`.
- `minimum` (an override, used by `compare_cells`) follows the policy rule: an integer of at
  least 1, a bool refused, else `ValueError`.
- `sample_cell` refuses (`ValueError`) a bad policy even in a field it does not use, for
  example a bad `min_denominator_comparison`.

### Rounding

`ratio` is the exact fraction `numerator / denominator` of the two integers, rounded half up
(away from zero) to 4 places: `Fraction(n, d)` quantized with `Decimal` `ROUND_HALF_UP`.
`difference` is the exact difference of the two fractions, `Fraction(ln, ld) -
Fraction(rn, rd)`, rounded the same way. A float is never divided or subtracted. The JSON
value is the float of that 4-place decimal.

Example: 1 of 32 is exactly 0.03125, so `ratio` is `0.0313`. (A float rounding,
`round(1 / 32, 4)`, gives `0.0312`.) A difference of -1/32 is `-0.0313`.

Why: HOM-84 must match a reference dataset exactly. A float rounding depends on the binary
form of the value, so two correct programs can disagree on the last digit.

Coverage counters use Python `round()` (`round(n / d, 4)`), trend ratios use half up, so the
last digit can differ between a coverage ratio and a trend ratio for the same counts. Both
keep their integers, and the integers are what reconcile.

SQL in HOM-80 returns integer counts only. Only this package computes a ratio or a
difference.

### Version 1 numbers

`SAMPLE_POLICY_V1` is `SamplePolicy(version="trend-sample-policy/1",
min_denominator_rate=30, min_denominator_agent=30, min_denominator_comparison=50,
hide_agent_counts_below_minimum=True)`.

| Kind | Minimum | Reason |
| --- | --- | --- |
| `rate` (one cohort: the workspace, a team, a language, a source) | 30 | With 30 conversations, the 95% interval of a rate is at most about ±18 points (1.96 × √(0.25/30) ≈ 0.18). Below that, a rate moves too much from call to call to read as a trend. |
| `agent` (one agent's own conversations) | 30 | The same statistics as a rate. A person is judged on it, so it is not lower. |
| each side of a comparison | 50, on top of its kind | A difference of two rates has more noise than one rate. With 50 per side, the 95% interval of the difference is at most about ±20 points (1.96 × √(2 × 0.25/50) ≈ 0.196). Below 50, a "team A is higher" claim is mostly noise. |

These are conservative starting numbers, not calibrated ones. Changing one is a new policy
version.

### Comparisons

`compare_cells(left, right, *, left_kind, right_kind, policy)` compares two cohorts. Each side
must reach `max(its kind's minimum, min_denominator_comparison)`. **Both sides must pass.**
The status is `not_computable` if a side is (left first), else `insufficient_sample` if a
side is (left first), else `computed`. `difference` is
the exact difference of the two fractions, rounded half up (see "Rounding"), only when
computed.

Example: team A 20 of 60, team B 9 of 40. Team B is under 50, so the comparison is
`insufficient_sample` and `difference` is `null`. Both cells still show their counts.
Example: team A 30 of 60, team B 20 of 50. Both pass. `difference` is 1/2 - 2/5 = 1/10,
`0.1`.

### Small counts stay visible

F49 says insufficient samples must be **visible**. So a rate cell under the minimum keeps its
numerator and denominator. Only the ratio is `null`.

An agent cell under the minimum keeps its denominator, but the numerator and the row's
`pending_review` are `null` (`hide_agent_counts_below_minimum`). Why: "1 complaint in 4
calls" points at single calls of one person and invites a judgment on too little data. The
denominator (how many calls) is not sensitive. `absent_pending_review` is hidden too, for
the same reason. The missing-data counts stay visible: they describe the data, not the
person.

Two things are HOM-82's job, not this package's, and HOM-82 does both ("HOM-82: role views
and disclosure"):

- **Complementary disclosure.** A hidden agent count can be recovered by subtraction, for
  example a team total minus the other agents. HOM-82 decides which cells a role sees
  together, so it stops that.
- **The kind of a cohort.** A cohort of one person (a team with one agent, a filter on one
  `agent_user_id`) is an agent cell. HOM-82 chooses the kind from the agents of the cohort.

## Correlation is not cause

The package has no field, status or word that says one trend causes another. A comparison
gives a `difference`, never a cause. HOM-83 must show any relation between two trends as
co-occurrence ("appears together with"), and attach the notice
`correlation_not_cause` (`ASSOCIATION_NOTICE`) to every view that shows two trends together.

For drill-down, HOM-83 reads the evidence link from the stored row: each kept value's
`finding_index` is its position among the kept values over intent, topic, entity and
outcome (`services/analysis_extraction.py:87-89`, `:120-129`).

## Versions and digests

- `TRENDS_VERSION` is `issue-trends/1`. It changes when a counting, filter or row rule of
  this README changes.
- The definition digest is sha256 over the canonical JSON of the parsed file (sorted keys,
  no spaces, UTF-8), like `taxonomy_digest`. Formatting does not change it. Any change of
  meaning does.
- `SAMPLE_POLICY_VERSION` is `trend-sample-policy/1`. `sample_policy_digest(policy)` is
  sha256 over the canonical JSON of the five policy fields.
- A test pins the digest of every released definition file, `sample_policy_digest(
  SAMPLE_POLICY_V1)` and every version constant. Editing a released file fails CI.
- Any change is a new file with the next version. Old files stay forever.
- Add an issue: a new version, with an id never used before.
- Change what an issue means (its references or its definition): not allowed. Add a new id
  and deprecate the old one.
- Deprecate: keep the issue with `status: "deprecated"`. It is never selected or counted.
- Remove: only after a released version deprecated it. Leave it out and add its id to
  `retired_issue_ids`. A retired id never comes back.
- An issue id is stable across versions, so a trend line can continue over a new version
  when the issue keeps its meaning.
- `load_definition` has no default version and there is no "latest" helper. A caller always
  pins one.

## Shapes for HOM-80 and HOM-81

### Per conversation: the input

`ConversationFacts`. HOM-80 loads it with set-based queries: the meeting's ledger row and
its media group (to resolve `media_group_id` and the counted copy), the current transcript
revision, the current analysis run and the `analysis_extractions` row of that run.

```json
{
  "meeting_id": "3f0c…",
  "ingestion_id": "i7c3…",
  "media_group_id": "i7c3…",
  "is_counted_copy": true,
  "current_revision_id": "r9f8…",
  "transcript_is_mock": false,
  "analysis_run_id": "a1b2…",
  "analysis_state": "completed",
  "analysis_revision_id": "r9f8…",
  "analysis_is_mock": false,
  "extraction_run_id": "a1b2…",
  "extraction": {
    "policy_version": "extraction-policy/1",
    "policy_digest": "<64 hex>",
    "taxonomy_id": "conversation",
    "taxonomy_version": 1,
    "taxonomy_digest": "<64 hex>",
    "validator_version": "analysis-taxonomy-validator/1",
    "valid": true,
    "needs_review": false,
    "issues": [],
    "fields": {
      "intent": {"verdict": "accepted", "status": "stated", "abstain_reason": null,
                 "review_required": false, "issues": [],
                 "values": [{"label": "report_dissatisfaction", "basis": "explicit",
                             "source_turn_indexes": [0], "confidence": 0.9,
                             "critical": false, "text": null,
                             "disposition": "auto_accepted", "review_reason": null}],
                 "withheld": []},
      "topic": {"verdict": "accepted", "status": "stated", "abstain_reason": null,
                "review_required": false, "issues": [],
                "values": [{"label": "refund", "...": "..."}, {"label": "invoice", "...": "..."}],
                "withheld": []},
      "entity": {"...": "not read"},
      "outcome": {"verdict": "abstained", "status": "insufficient_evidence",
                  "abstain_reason": "model", "review_required": false, "issues": [],
                  "values": [], "withheld": []}
    }
  }
}
```

`extraction` is the stored `result`, unchanged (any JSON value; `null` with no row). The
judge checks the entity field's shape but never keeps or compares a label, a text or a
confidence of it.

`judge_conversation` raises `ValueError` when the facts break their own contract:
`extraction_run_id` is not `analysis_run_id`; a row without a run; `analysis_state` or
`analysis_revision_id` set without a run, or missing with one; `analysis_is_mock` true
without a run; an `analysis_state` outside `STAGE_STATES`; an `extraction` that is not
`null` without a row.

### Per conversation: the output

`ConversationJudgment.to_json()`:

```json
{
  "meeting_id": "3f0c…",
  "media_group_id": "i7c3…",
  "trends_version": "issue-trends/1",
  "definition": {"id": "trends", "version": 1, "digest": "<64 hex>"},
  "exclusion": null,
  "bucket": null,
  "unreadable": null,
  "dimensions": {
    "intent": {"bucket": null},
    "topic": {"bucket": null},
    "outcome": {"bucket": "abstained_insufficient_evidence"}
  },
  "issues": {
    "intent_report_dissatisfaction": {"state": "counted", "bucket": null,
                                      "absent_pending_review": false},
    "intent_request_support": {"state": "absent", "bucket": null,
                               "absent_pending_review": false},
    "outcome_unresolved": {"state": "missing", "bucket": "abstained_insufficient_evidence",
                           "absent_pending_review": false},
    "complaint_refund": {"state": "counted", "bucket": null, "absent_pending_review": false},
    "complaint_invoice": {"state": "counted", "bucket": null, "absent_pending_review": false},
    "complaint_pricing": {"state": "absent", "bucket": null, "absent_pending_review": false}
  },
  "families": {
    "call_reason": {"state": "counted", "bucket": null, "absent_pending_review": false},
    "unresolved_outcome": {"state": "missing", "bucket": "abstained_insufficient_evidence",
                           "absent_pending_review": false},
    "service_complaint": {"state": "counted", "bucket": null, "absent_pending_review": false}
  }
}
```

`issues` has every active issue of an available family, and `families` every available
family (the example shows some). An excluded conversation has `exclusion:
"duplicate_media"`, `bucket: null` and empty `dimensions`, `issues` and `families`. A
conversation stopped at steps 2 to 9 has that `bucket`, and every dimension, issue and
family is `missing` with the same bucket. `unreadable` is set exactly when `bucket` is
`extraction_unreadable`.

### The cell

```json
{"status": "computed", "numerator": 14, "denominator": 120, "ratio": 0.1167,
 "reason": null, "minimum_sample": 30}
```

### The normalized filter object

`TrendFilters.to_json()`:

```json
{
  "window": {"from": "2026-09-23T00:00:00+00:00", "to": "2026-10-23T00:00:00+00:00",
             "basis": "ingestion_received_at"},
  "team_id": ["7d9e2c1a-0b4f-4e8a-9c3d-5f6a7b8c9d0e"],
  "agent_user_id": [],
  "language": ["fa"],
  "source_type": [],
  "family": [],
  "issue": [],
  "service_area": ["refund"]
}
```

An empty list means no restriction. HOM-80 turns each population list into an `IN (...)`
on the field of the table above, and the window into `from <= received_at < to`.

### A trend answer

HOM-80 builds the rows with `build_rows(tally, definition, filters, kind=..., policy=...)` and
the provenance with `trend_provenance(definition, policy)`. HOM-81 adds `coverage`, `scorecard`
and `missing_data` (`add_trend_metadata` in `services/issue_trend_service.py`). The answer of
`GET /api/v1/trends/issues` below is shortened: `filters`, `scope`, `series` and `points` are
in "The answer" of HOM-80, and every counter has the full `coverage/1` keys.

```json
{
  "trends_version": "issue-trends/1",
  "definition": {"id": "trends", "version": 1, "digest": "<64 hex>"},
  "taxonomy": {"id": "conversation", "version": 1, "digest": "<64 hex>"},
  "extraction_policies": [{"version": "extraction-policy/1", "digest": "<64 hex>"}],
  "validator_versions": ["analysis-taxonomy-validator/1"],
  "sample_policy": {"version": "trend-sample-policy/1", "digest": "<64 hex>"},
  "window": {"from": "2026-09-23T00:00:00+00:00", "to": "2026-10-23T00:00:00+00:00",
             "basis": "ingestion_received_at"},
  "filters": {"...": "the normalized filter object"},
  "population": {"included": 120, "excluded": {"duplicate_media": 2}},
  "coverage": {"version": "coverage/1",
               "window": {"from": "2026-09-23T00:00:00+00:00",
                          "to": "2026-10-23T00:00:00+00:00",
                          "basis": "ingestion_received_at"},
               "basis": "workspace",
               "processing": {"numerator": 122, "denominator": 130, "ratio": 0.9385,
                              "status": "computed", "reason": null, "...": "..."},
               "evaluation": {"numerator": 101, "denominator": 122, "ratio": 0.8279,
                              "status": "computed", "reason": null, "...": "..."}},
  "scorecard": {"status": "not_available", "version": null, "digest": null,
                "reason": "scorecard_results_unavailable"},
  "missing_data": {"indicators": ["evaluation_incomplete", "missing_values",
                                  "pending_review", "processing_incomplete",
                                  "scorecard_unavailable", "source_unavailable"]},
  "rows": [
    {"level": "family", "family": "service_complaint", "issue": null,
     "cell": {"status": "computed", "numerator": 14, "denominator": 96, "ratio": 0.1458,
              "reason": null, "minimum_sample": 30},
     "pending_review": 0,
     "absent_pending_review": 0,
     "missing": {"analysis_unavailable": 6, "analysis_stale": 0, "mock_analysis": 0,
                 "extraction_unavailable": 3, "extraction_unreadable": 0,
                 "version_mismatch": 0, "extraction_invalid": 2,
                 "abstained_insufficient_evidence": 9, "abstained_contradictory": 1,
                 "abstained_contradictory_values": 0, "abstained_low_confidence": 3,
                 "value_withheld": 0}},
    {"level": "family", "family": "qa", "issue": null,
     "cell": {"status": "not_computable", "numerator": null, "denominator": 120,
              "ratio": null, "reason": "scorecard_results_unavailable",
              "minimum_sample": 30},
     "pending_review": null, "absent_pending_review": null, "missing": {}},
    {"level": "issue", "family": "call_reason", "issue": "intent_cancel_service",
     "cell": {"status": "computed", "numerator": 0, "denominator": 104, "ratio": 0.0,
              "reason": null, "minimum_sample": 30},
     "pending_review": 7, "absent_pending_review": 2, "missing": {"...": "every bucket"}}
  ]
}
```

In every available row: `cell.denominator + sum(missing) == population.included`. In the
`intent_cancel_service` row, the 7 calls with a pending `cancel_service` are its
`pending_review`; the same 7 calls are `absent_pending_review` in each of the other seven
call-reason rows. Its own `absent_pending_review` (2) is the calls whose intent is a pending
`unsolicited`.

`scorecard` is the same block in `GET /admin/coverage`. Scorecards (HOM-54) do not exist yet,
so it is always `not_available` with `scorecard_results_unavailable` and never a version;
HOM-54 adds `available`. `missing_data.indicators` is a sorted list of distinct codes. Each
code is a pure function of other fields of the same answer (`services/aggregate_metadata.py`),
never a new query or a new number:

| Code | When |
| --- | --- |
| `processing_incomplete`, `evaluation_incomplete` | that `coverage` counter is `computed` and `numerator < denominator` |
| `processing_unavailable`, `evaluation_unavailable` | that counter is `not_computable` for a reason other than `empty_denominator` |
| `scorecard_unavailable` | `scorecard.status == "not_available"` (always today) |
| `empty_population` | `population.included == 0` |
| `missing_values` | a row of `rows` has `sum(missing) > 0` |
| `pending_review` | a row of `rows` has `pending_review > 0` or `absent_pending_review > 0` (`null` is 0) |
| `insufficient_sample` | a row of `rows` has `cell.status == "insufficient_sample"` |
| `source_unavailable` | a row of `rows` has `cell.status == "not_computable"` for a reason other than `empty_denominator` (the `qa` row, always, while it is selected) |

Only `rows` of the whole window count, not `points[].rows`; the points are unchanged.
`GET /admin/coverage` uses the same counter rules on its four counters (so it can also hold
`ingestion_*` and `review_*` codes; `review_unavailable` is always there today), plus
`scorecard_unavailable` and `empty_population` (`counters.ingestion.denominator == 0`).

### The coverage link

The trend population is the `coverage/1` processing `transcribed` bucket of the same window,
so with no population filter:

`population.included + sum(population.excluded) == coverage.processing.numerator`.

HOM-81 shows the processing and evaluation counters of the same window next to the trend
(`coverage`), so a reader sees how many accepted conversations were transcribed and evaluated
before any issue was counted. `coverage.window` is the answer's `window`. `coverage.basis` says
which ledger rows the counters count:

- `workspace`: the caller is unrestricted (`scope.restricted` is false) and the query has no
  population filter (`team_id`, `agent_user_id`, `language`, `source_type` all empty). The
  counters are equal to the `processing` and `evaluation` counters of `GET /admin/coverage`
  for the same `from` and `to`.
- `trend_population`: otherwise. The same `compute_coverage` rules count only the accepted
  ledger rows of the window that join to a meeting of the workspace (live or soft-deleted)
  whose meeting passes the caller's `meeting_clause()` and the `team_id`, `agent_user_id` and
  `language` filters, and whose ledger row passes the `source_type` filter
  (`compute_coverage(..., population=clauses)`). A ledger row without a meeting, or a
  rejected row, cannot be attributed and is not counted. So a restricted caller never sees a
  count about a meeting outside their scope.

Selectors (`family`, `issue`, `service_area`), `grain` and `timezone` never change the block.
With both bases, and any filter:

- `population.included + sum(population.excluded) == coverage.processing.numerator` when
  `coverage.processing.status` is `computed`; both are 0 otherwise (`numerator` is 0, not
  `null`, for an empty denominator);
- `coverage.evaluation.denominator == coverage.processing.numerator`.

There is no `ingestion` and no `review` counter in this block. The rejected rows are in the
`ingestion` counter of `GET /admin/coverage` only.

## How HOM-80 to HOM-84 use it

1. Pin a definition: `load_definition("trends", 1)`. Store its version and digest with every
   answer (`trend_provenance`).
2. Validate the query: `validate_filters(params, definition, now=now)`. Map each error code
   to a 422, like `coverage_window_invalid`.
3. HOM-80 decides the counted copy of every media group over the whole workspace, selects
   the population in SQL (above), applies the caller's scope and the population filters by
   AND, and loads `ConversationFacts` with a fixed number of queries.
4. Judge and add up: `tally([judge_conversation(f, definition) for f in facts], definition)`.
   HOM-80 may count in SQL instead. Then HOM-84 checks that the SQL counts equal `tally` on
   the reference dataset, issue by issue and bucket by bucket.
5. `build_rows(...)` with `kind=CellKind.AGENT` for a cohort of one agent, else `RATE`.
   `compare_cells` for two cohorts.
6. HOM-83 drill-down lists the conversations of one issue state, with their
   `ConversationJudgment`, reads the evidence of each value through its `finding_index`, and
   shows `correlation_not_cause` next to two trends.

## HOM-80: the scoped aggregates

HOM-80 is the first backend user of this package. It changes no rule above.

| Part | Where |
| --- | --- |
| Service | `services/issue_trend_service.py`: `compute_issue_trends`, `validate_series`, `TREND_GRAINS`, `TREND_DEFAULT_TIMEZONE`, `TrendSeriesError` |
| Route | `routers/v1/trends.py`: `GET /api/v1/trends/issues` |
| Response model | `schemas/trends.py`: `IssueTrendResponse` |
| Shared population helpers | `services/coverage_service.py`: `transcribable_clauses`, `has_segments` |

### The route

`GET /api/v1/trends/issues`. Permission `meeting:read`, plus the caller's record scope
(`RecordAccess.meeting_clause()` of the request context), like `GET /voicemails/analytics`.
Owners and admins see the whole workspace. A member or a viewer of a workspace with
`record_scope = team` sees only the meetings of the teams they supervise and the meetings
attributed to them (agent or uploader). An API key with the `meeting:read` scope may read it
too. It is a read and it is not audited, like coverage.

Query parameters:

- every key except `grain` and `timezone` goes to `validate_filters` ("Supported filters").
  A key given once is a string; a key given more than once is a list
  (`language=fa&language=en`). So `from` twice is `filter_bad_type`;
- `grain`: `none` (default), `day`, `week` or `month`;
- `timezone`: an exact, case-sensitive IANA name from `zoneinfo.available_timezones()`.
  Default `Asia/Tehran`. `UTC` is accepted; `utc` and `asia/tehran` are refused on every
  platform (a plain `ZoneInfo("utc")` succeeds on a case-insensitive file system).

### Errors

Every refusal of the route itself is a `422` in the app's error envelope:
`{"error": {"code": ..., "message": ..., "request_id": ..., "details": {"key": ...}}}`. The
code is `error.code` and the query key at fault is `error.details.key`.

| Code | `details.key` | When |
| --- | --- | --- |
| `filter_scope_not_allowed` | the key | `workspace_id`, `organization_id`, `role` or `scope` in the query |
| `filter_unsupported` | the key | `product`, `customer_id`, `sentiment` and the other words of "Supported filters" |
| `filter_unknown_key` | the key | any other key that is not a filter, `grain` or `timezone` |
| `filter_bad_type` | the key | `from` or `to` given more than once |
| `filter_bad_value` | the key | a value outside the allowed values, an empty value, more than 50 values |
| `filter_window_invalid` | `from`, `to` or `null` | a window rule; `null` when the two ends together break it |
| `trend_grain_invalid` | `grain` | not one of the four grains, or `grain` given more than once |
| `trend_timezone_invalid` | `timezone` | not an exact IANA name, given more than once, or a window that cannot be shown in that zone (near year 1 or 9999, only with a series) |

Order: the filter codes first, in their own order ("Supported filters"), then
`trend_grain_invalid`, then `trend_timezone_invalid`. So `workspace_id=x&grain=hour` is
`filter_scope_not_allowed`. Before any of them, `require_permission` answers `401` without a
session and `403 workspace_access_denied` without a membership in the workspace.

### Scope is never a filter (v1 answer)

The workspace and the record scope are ANDed with every filter, so a filter only narrows. A
`team_id` outside the caller's scope answers `200`, narrowed to what the caller may see
(often empty), never `403`, and it never tells whether that team has data. This is the
answer the other scoped routes give. HOM-82 keeps it for this route (open question 6) and
adds one refusal: `agent_user_id` other than the caller's own id is `403
agent_scope_denied`.

### The answer

```json
{
  "trends_version": "issue-trends/1",
  "definition": {"id": "trends", "version": 1, "digest": "<64 hex>"},
  "taxonomy": {"id": "conversation", "version": 1, "digest": "<64 hex>"},
  "extraction_policies": [{"version": "extraction-policy/1", "digest": "<64 hex>"}],
  "validator_versions": ["analysis-taxonomy-validator/1"],
  "sample_policy": {"version": "trend-sample-policy/1", "digest": "<64 hex>"},
  "window": {"from": "2026-09-30T20:30:00+00:00", "to": "2026-10-02T20:30:00+00:00",
             "basis": "ingestion_received_at"},
  "filters": {"window": {"...": "the same window"}, "team_id": [], "agent_user_id": [],
              "language": [], "source_type": [], "family": [], "issue": [],
              "service_area": []},
  "scope": {"restricted": false},
  "series": {"grain": "day", "timezone": "Asia/Tehran", "calendar": "gregorian",
             "week_starts_on": "saturday"},
  "population": {"included": 120, "excluded": {"duplicate_media": 2}},
  "rows": ["... build_rows output for the whole window, as in 'A trend answer' ..."],
  "points": [
    {"from": "2026-10-01T00:00:00+03:30", "to": "2026-10-02T00:00:00+03:30",
     "partial": false,
     "population": {"included": 64, "excluded": {"duplicate_media": 1}},
     "rows": ["... build_rows output for this point ..."]},
    {"from": "2026-10-02T00:00:00+03:30", "to": "2026-10-03T00:00:00+03:30",
     "partial": false,
     "population": {"included": 56, "excluded": {"duplicate_media": 1}},
     "rows": ["..."]}
  ]
}
```

- The provenance keys are exactly `trend_provenance(definition, SAMPLE_POLICY_V1)`.
- `window` and `filters.window` are both `TrendWindow.to_json()`: strings in UTC (`+00:00`).
- `scope.restricted` is `RecordAccess.restricted`.
- `rows` is `build_rows(...)`, family rows then issue rows, each `TrendRow.to_json()`. The
  `qa` row is always `not_computable` with `scorecard_results_unavailable`, never 0.
- `points` is `[]` when `grain` is `none`. `series` is always there.
- `compute_issue_trends` returns no `coverage` key. The route adds `coverage`, `scorecard`
  and `missing_data` with `add_trend_metadata` (HOM-81, "A trend answer").

Invariants (tested in `tests/test_issue_trend_service.py`):

- in every available row of the window and of every point,
  `cell.denominator + sum(missing) == population.included`;
- the points add up to the window for `population.included`, `excluded.duplicate_media`,
  every row's `cell.denominator` and every `missing` bucket, always; and for
  `cell.numerator`, `pending_review` and `absent_pending_review` wherever no value is `null`.
  A `null` comes only from the agent-cell rule ("Small counts stay visible") or the `qa` row;
- with an unrestricted caller and no population filter,
  `population.included + population.excluded.duplicate_media` is the `coverage/1`
  processing numerator of the same window ("The coverage link").

### Series

- Points are cut at local boundaries of `timezone`: `day` at every local midnight, `week` at
  local midnight of every **Saturday** (the Iranian week), `month` at local midnight of day 1
  of every **Gregorian** month.
- A local midnight is `datetime(y, m, d, tzinfo=zone)` with fold 0 (PEP 495). A midnight in a
  DST gap is then the first instant of that local day. Iran has no DST since 2022.
- The first point starts at `window.from` and the last ends at `window.to`. A point cut by the
  window is `partial: true`; every other point is a whole day, week or month.
- `from` and `to` of a point are shown in the series timezone
  (`2026-10-01T00:00:00+03:30`). A call at 23:00 UTC is at 02:30 the next day in Tehran, so it
  belongs to the next local day.
- A conversation belongs to the point that holds its own `received_at`. An included
  conversation is its own counted copy, so this is the point of the counted copy. An excluded
  duplicate is counted in the point of its own `received_at`.
- Each conversation is judged once. Its judgment goes into the window tally and into the
  tally of its point. Each point then gets its own `build_rows`, with the same selectors and
  cell kind.
- There is no cap on the number of points: `grain=day` over 366 days gives up to 367 points
  (see "Cost").

### Cell kind

Since HOM-82 the kind comes from the data, not from the filter: `CellKind.AGENT` when the
population is the caller's own calls, or when it holds 1 to 4 distinct agents other than the
caller (a small group); else `CellKind.RATE`. The same kind for the window and every point
("HOM-82: role views and disclosure", rule 1).

HOM-80 used `CellKind.AGENT` when the `agent_user_id` filter had exactly one value. A caller
could then add a second `agent_user_id` that owns no meeting: the conversations stayed the
same, but the kind became `rate`, so the counts an agent cell under the minimum hides became
visible (L1). The kind from the data closes it.

### How it counts

1. The population (statement P): the ledger rows of the window in the coverage `transcribed`
   bucket (`transcribable_clauses` in SQL, then `has_segments` on the current revision),
   ANDed with `meeting_clause()` and each non-empty population filter as `IN (...)`. The same
   statement LEFT JOINs the current transcript revision, the current analysis run
   (`is_current`) and that run's `analysis_extractions` row. An older run and its row are
   never read. One row per conversation: `uq_ingestion_meeting`,
   `uq_transcript_revision_current`, `uq_analysis_run_current` and
   `uq_analysis_extraction_run` guarantee it, and `tally` refuses a meeting judged twice.
2. The media groups (statement G): a recursive query walks every linked ledger row of the
   **whole workspace** up its `duplicate_of_id` chain to the root. For each group that holds a
   row of the window, it returns every member's `received_at` and transcribed state (the same
   two helpers, without the window). Members outside the window, the filters or the scope
   are read only for this; nothing about them is returned. `counted_copy_id(members)` picks the
   counted copy in Python.
3. `ConversationFacts` per row, then `judge_conversation` and `tally` in Python, then
   `build_rows`. SQL returns rows only: no count, no division, no rounding.

Why Python counting and not SQL JSON counting (the choice "What HOM-80 reads from the
database" asks for): the steps, the shape walk with its one-reason order, the status mapping
and `absent_pending_review` then have one source, this package. A SQL copy would be a second
definition that HOM-84 would have to reconcile bucket by bucket. `analysis_extractions.result`
is `JSON`, not `JSONB`, so SQLite and Postgres would need two JSON dialects; Python gives one
answer on both. Only the Python path was built and measured.

### Cost

The service runs exactly **2 statements** (P and G), whatever the number of conversations,
groups or points, with or without a series, scoped or not (pinned by
`test_the_statement_count_is_fixed`). The route adds the fixed statements of
`require_permission` (session, membership, tenant binding, team assignments).

Measured on 2026-10-23 on Postgres 17 (local container, 8-core Apple silicon laptop with a
load average of 12 to 15 from other test suites, so the absolute times are high). One
workspace with 10 000 conversations over 30 days: 2 teams, 203 duplicates (chains of 2 and
3), 302 stale runs, 203 without a run, 320 without a row, 107 unreadable rows. The route was
called with an owner session, 20 calls after one warm-up:

| Request | Median | p95 | Route statements | Response | Points |
| --- | --- | --- | --- | --- | --- |
| 30 days, `grain=none` | 4.37 s | 5.91 s | 21 | 15 KB | 0 |
| 30 days, `grain=day` | 3.80 s | 4.50 s | 21 | 440 KB | 30 |
| 366 days, `grain=day` | 3.29 s | 6.30 s | 21 | 5.3 MB | 366 |

The 21 route statements are the 2 of the service plus the fixed statements of the session,
membership, tenant binding and record access; the count is the same for every request.
Since HOM-81 the route also runs `add_trend_metadata`: 1 ledger statement, plus 8 fact
statements when a candidate exists, so up to 9 more statements. The times above were
measured before HOM-81.

Where the time goes (service only, median of 5, 30 days, `grain=none`): loading 0.83 s (both
statements, decoding about 9 700 stored `result` JSON values, building the facts), judging
1.40 s (about 140 µs per conversation, the shape walk of `judge_conversation`), `tally` and
`build_rows` 0.10 s. In Postgres itself, `EXPLAIN ANALYZE` gives 24 ms for P and 21 ms for G.
So almost all of the cost is Python, not SQL.

For HOM-84: this path does not meet the proposed p95 under 1 s at 10 000 conversations on
this machine. The cost grows with the conversations of the window, not with the points (the
366-day series has the same conversations). Ways to lower it, none built here: counting in SQL
with a test that it equals `tally` bucket by bucket; storing the judgment of each immutable
extraction row (a new table, so a migration); caching judgments in the process (a row never
changes). Each one is a decision for HOM-84.

No index is missing: P and G each take about 20 ms in Postgres. G starts from the workspace's
linked rows (`duplicate_of_id IS NOT NULL`) and walks up by primary key. No migration.

### Known limits

- **Two statements, two snapshots.** Under Postgres `READ COMMITTED` statements P and G each
  take their own snapshot. If a member of a media group finishes transcription or is deleted
  between them, one answer can classify that duplicate with slightly old data. It never
  counts a group twice: one counted copy is chosen per group, so at most one row of a group is
  included.
- **`duplicate_of_id` is `ON DELETE SET NULL`.** If a ledger row were deleted, its linked rows
  would become roots and a media group could split in two. No backend code deletes
  `ingestion_records`; only a workspace or organization cascade does, and that removes the
  whole group. A future ledger deletion must keep the groups whole.
- **A `duplicate_of_id` loop.** No caller can make one (a link always points to an earlier
  row), but a manual edit, a backfill or a future bug could (A links to B, B links to A).
  Statement G's recursion is a `UNION`, which drops a repeated pair, so it always ends. A row
  in a loop, or a row that links into one, never reaches a root: each such row counts as its
  own group, so the copies of that media can count more than once. Other groups are not
  affected.

### What HOM-81 and HOM-82 add

- HOM-81 (done): the route answer has `coverage`, `scorecard` and `missing_data` ("A trend
  answer", "The coverage link"), and `GET /admin/coverage` has `scorecard` and
  `missing_data`. Every other key of both answers is unchanged. `compute_issue_trends` itself
  is unchanged: it still returns no `coverage` key and still runs 2 statements. The route then
  calls `add_trend_metadata`, which adds the three keys with `compute_coverage`: 1 ledger
  statement, plus 8 fact statements when a meeting of the window may be transcribed. That
  count does not grow with the conversations (pinned in `tests/test_aggregate_metadata.py`).
  `pending_review`, `absent_pending_review` and the `missing` buckets were already next to
  each rate; `missing_data` sums them up for the whole answer. The rendering is frontend
  scope (HOM-237).
- HOM-82 (done) builds the role views (supervisor, executive, analyst, agent self-view) on
  this service, chooses the kind from the data, and stops complementary disclosure of hidden
  agent counts, including the extra `agent_user_id` that turned an agent cell into a rate
  cell ("HOM-82: role views and disclosure").

## HOM-82: role views and disclosure

HOM-82 (WP15-T04) adds four read routes on the same population and rules, and the rules that
keep an aggregate from showing one person. It changes no counting rule of this package, so
`TRENDS_VERSION` stays `issue-trends/1`; the disclosure rules have their own version,
`trend-disclosure/1` (`DISCLOSURE_VERSION`), with one new constant, `MIN_COHORT_AGENTS = 5`
(`samples.py`; not a field of `SAMPLE_POLICY_V1`). ADR 0038 records the decisions.

| Route | Who | Cohorts |
| --- | --- | --- |
| `GET /api/v1/trends/teams` | workspace owner or admin (every active team); member or viewer with an effective supervisor assignment (those teams only); a session only | one block per compared team (at most 10), and pairwise comparisons |
| `GET /api/v1/trends/me` | any workspace member; a session only | the caller's own meetings as agent (`agent_user_id = caller`; uploads do not count) |
| `GET /api/v1/organizations/{organization_id}/trends/issues` | organization owner, admin, analyst, executive (`organization:analytics:read`) | one block per scoped workspace (the grant unit) |
| `GET /api/v1/organizations/{organization_id}/workspaces/{workspace_id}/trends/issues` | organization owner, admin, analyst (`organization:records:read`) | the grant unit and one block per active team inside it |

"A session only": every bearer that looks like an API key gets `401 api_key_not_accepted`
before any key lookup. `GET /trends/issues` still accepts keys.

The code: the pure rules are in `services/issue_trend_service.py` (section "disclosure") and
the views in `services/role_trend_service.py`. Each view loads one scope with statements P
and G, judges every conversation once and splits cohorts in Python.

### The rules

**The agent of a conversation** is `Meeting.agent_user_id`. A meeting with no agent counts
in every number but adds no agent. **A small group** has 1 to 4 distinct agents among its
included conversations. On a workspace view the caller is not counted (they know their own
calls); on an organization view they are.

1. **Kind from the data** (workspace views). No agent: `rate`. Only the caller's own calls
   (and the self-view always): `agent`. A small group: `agent`, with rule 2. Else `rate`.
2. **All or nothing** (workspace views, an `agent` cohort seen by someone other than its
   only agent). If one cell of the window rows is under the minimum (hidden), every other cell
   of the window rows is withheld; the same for the cells of all points, decided on their own.
   When the caller is one of the agents, the cohort without their calls is tested too. Every
   row of the definition is tested, whatever the selectors. Why the whole layer: in
   `call_reason` and `unresolved_outcome` the family numerator is the sum of its issues, and
   every complaint is also an `intent_report_dissatisfaction`.
3. **One canonical released-team set** (workspace views with team cohorts). For one caller,
   window and set of non-team filters, over every team that has a conversation in the
   caller's scope (also inactive teams and teams the caller may not compare): the remainder
   (the scope minus the released teams minus the caller's own calls) must not be unsafe.
   Unsafe: 1 to 4 agents and a cell under the minimum that holds an agent. While it is
   unsafe, the team with the fewest included conversations (then the smallest id) moves into
   it. A points pass runs the same way. `GET /trends/teams` shows a team only from this set;
   `GET /trends/issues?team_id=` is released only when every requested team with data is in
   it. So subtracting released answers always leaves the same safe remainder plus whole
   released teams.
4. **Organization views: withhold, per workspace and week.** The window is whole closed
   weeks from Saturday 00:00 Asia/Tehran (default the 4 closed weeks before the current one,
   at most 52; grain `none` or `week`; no `timezone`, `team_id`, `agent_user_id`, `language`
   or `source_type`). A cohort passes a week when its included conversations, and the
   denominator of every row (whatever the selectors), have 0 or at least 5 agents. Per
   workspace and week, over every team id with data that week (active or not), the passing
   teams form `Rel_k`; if the workspace minus `Rel_k` does not pass, `Rel_k` is empty. A
   whole-workspace block is released when it passes every week; a team block or a team-grant
   block when each of its teams with data is in `Rel_k` every week (an empty team-week
   passes). A caller who also holds an effective, restricted membership in the workspace
   (not owner or admin, `record_scope=team`) knows every conversation of its scope S
   (supervised teams, own agent meetings, own uploads): every block of that workspace must
   then also pass the week test on the block minus S, in every week, or it is withheld
   (`complementary_disclosure`). No membership, or an unrestricted one, changes nothing (A6).
   A withheld block has no count at all: `population`, `coverage` and
   `missing_data` are `null`, `rows` and `points` empty.

`disclosure` of every cohort: `status` (`released`, `masked`, `withheld`), `reason`
(`small_group`, `complementary_disclosure` or `null`), `kind` and `masked` (the withheld
layers, `points` and `window`). A withheld cell has status `withheld`, reason
`complementary_disclosure`, a `null` numerator, ratio, `pending_review` and
`absent_pending_review`, and keeps its denominator and missing counts.

### Comparisons

`GET /trends/teams` compares every pair of teams per window row with `compare_cells` (50 per
side), only between `rate` teams of the canonical set. The status of each row comes in this
order: a side of kind `agent` gives `withheld` (`small_group`); then a withheld side gives
`withheld` (`complementary_disclosure`); then the `qa` row gives `not_computable`
(`scorecard_results_unavailable`); then `compare_cells`. So a small group is never compared,
on any row, the `qa` row included. No rank field; teams in a fixed order (name case-folded, then
id); `difference` is left minus right in that order. The notices `correlation_not_cause` and
`unadjusted_comparison` go with every comparison answer, and `mix` shows the languages and
sources of each team.

### Refusals

The workspace views check, in order: the credentials, the key classes
(`filter_scope_not_allowed`, then `trend_parameter_not_allowed`, then `filter_unsupported`,
then `filter_unknown_key`), the values (`filter_bad_type`, `filter_bad_value`,
`filter_window_invalid`), the series, `trend_too_many_teams`, then `403 team_scope_denied`.
An owner or admin of a workspace with no active team, with no `team_id`, gets `200` with
`teams: []` and `comparisons: []`: there is nothing to compare. A member or viewer with no
comparable team gets the 403, and a named `team_id` outside the set is a 403 for every role.
The organization views check `require_organization`, the permission, the key classes, the
values after the grid default for a missing end, `trend_grain_invalid`, then
`trend_window_not_aligned` (`from`, then `to`, then `to` closed), then `404
workspace_not_found`. `team_scope_denied` and `agent_scope_denied` also mean a refused
attribution write (`services/team_service.py`); here they mean a refused read scope.

### Cost

The statement counts do not grow with the conversations or the teams. `tests/test_role_trend_routes.py`
pins these numbers of query statements per request (the `set_config` calls of the
row-level-security binding, PostgreSQL only, are left out, so one number holds on SQLite and
PostgreSQL; on PostgreSQL the binding adds 16 to `GET /trends/issues`):

| Request | Statements pinned |
| --- | --- |
| `GET /trends/issues`, unrestricted caller, with or without `team_id` | F = 14: 3 (session, user, membership) + 2 (P and G) + 9 (coverage) |
| `GET /trends/me` | F + 0 = 14 |
| `GET /trends/teams`, owner, 2 or 3 teams | F + 1 = 15 (the active teams; `compute_coverage_by_team` is the same 9 for every team) |
| `GET /trends/teams`, a member who supervises | F + 2 = 16 (the active teams and their supervisor assignments) |
| executive view, per released workspace, caller with no membership there | + 12 (P, G, the caller's membership (A6) and 9 coverage statements; rule 8: each workspace is its own tenant) |
| drill-down, 1 or 3 teams, caller with an unrestricted membership | 17: 3 (session, user, organization membership) + 1 (the workspace) + 1 (its teams) + 2 (P and G) + 1 (the caller's membership) + 9 (one grouped coverage run) |

A caller with a restricted membership in the workspace adds 2 more statements there (its team
assignments and the meetings of its scope), whatever the weeks and teams.

A withheld executive block runs no coverage run. In Python, `GET /trends/issues` with `team_id`
now judges the caller's whole scope, and an organization team grant judges the whole workspace.

### Known limits

- Workspace views, across answers with other windows, grains, languages or sources, can
  still isolate a few calls; the caller may open those records (record scope).
- Inside a `rate` cohort on a workspace view, a day point or a row can rest on one agent and
  show a numerator under 30.
- A restricted caller's scope changes over time (an assignment ends, a team becomes
  inactive); the canonical set holds only within one scope.
- On organization views a closed week can still change (a transcription finishes, a meeting
  is deleted, moved to another team or agent, or its team is deleted), so two answers at two
  times can differ by a few conversations.
- A team-grant block reveals one yes or no per week about the rest of the workspace.
- An organization caller with a restricted workspace membership is checked against that
  membership's scope (A6); knowledge from outside the system is not.
- Dominance (one agent with almost all calls of a cohort) is not checked.

## Open questions

These are product choices. Version 1 uses the default given here.

1. Sample minimums 30 (rate), 30 (agent), 50 (each side of a comparison). Default: these.
2. An agent cell below the minimum hides its numerator, `pending_review` and
   `absent_pending_review`. Should a manager see them anyway? Default: hidden for everyone.
   HOM-82 keeps it, applies it to small groups of 1 to 4 agents too, and shows a manager no
   per-agent trend of another person (`agent_user_id` only the caller's own).
3. `intent_cancel_service` and `intent_unsolicited` show 0 confirmed until reviews exist,
   because policy 1 never auto-accepts a critical label. Default: shown as pending review.
4. A complaint is only the main intent `report_dissatisfaction`. A billing call that also
   complains is a billing call. Default: this.
5. `unresolved_outcome` includes `escalated` and `follow_up_scheduled`, not only
   `unresolved` ("not resolved in this call"). Default: this.
6. A `team_id` outside the caller's scope (HOM-82): 403 or an empty answer? v1 answer
   (HOM-80): `200`, narrowed to what the caller may see (often empty), never `403`, like the
   other scoped routes. HOM-82 answer: a population filter narrows (`200` on
   `GET /trends/issues` and `GET /trends/me`); a named cohort outside the scope is refused
   (`403 team_scope_denied` on `GET /trends/teams`); the organization views take no
   `team_id`.
7. The grain and timezone of a time series (HOM-80). v1 answer: `grain` `none` (default),
   `day`, `week` or `month`, cut in `timezone` (default `Asia/Tehran`), weeks starting on
   Saturday, Gregorian months. The owner may later confirm the default timezone and ask for
   Solar Hijri months; either is a new series option, not a new rule version.
