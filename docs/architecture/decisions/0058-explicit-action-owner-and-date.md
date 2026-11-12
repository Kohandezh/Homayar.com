# ADR 0058: An action's owner and due date are kept only when the call states them

Date: 2026-11-12 · Status: Proposed (HOM-133, WP24-T01) · Builds on: ADR 0014, ADR 0054

All code paths are under `Homayar/backend/`.

## Context

Feature F38 (parent HOM-27) asks the analysis to extract commitments and action items
from a call. Its acceptance: owner and date precision of at least 95% on held-out
examples; an owner or date the call does not state stays unset; every item links its
evidence and its transcript revision; a proposed next step is labelled apart from a
commitment.

Before this change, `jobs/processor.py` `persist_analysis` stored the provider's `owner`
and `due_date` as given. Nothing checked that a speaker said them. A provider could
invent a name or a date, and the reviewer would see it as a fact. An action with no
valid turn reference was still stored, with no evidence.

## Decision

### The provider output (`providers/schemas.py`)

`ActionItem` gains three optional fields:

- `kind`: `commitment` or `next_step` (`ActionKind`). The default is `next_step`, so an
  item without a label is never counted as a commitment.
- `owner_quote` (at most 500 characters): the exact words in a cited turn that name the
  owner.
- `due_quote` (at most 200 characters): the exact words in a cited turn that state the
  due date.

### The grounding rules (`services/action_grounding.py`)

`ground_action(action, turns, *, reference, language)` returns a `GroundedAction` or
`None`. `turns` are the turns the provider received (maybe redacted). `norm(x)` is
`normalize_for_matching(x, language)` with the outer spaces removed. The cited turns are
`turns[i]` for each `i` in `source_turn_indexes` that is in range.

- **G1.** No cited turn (an empty list, or every index out of range): `None`. The item
  is not stored. So every stored AI action has evidence.
- **G2.** The owner is kept only when: `owner` and `owner_quote` are not blank;
  `norm(owner_quote)` is not empty and is inside `norm(T.text)` for a cited turn `T`;
  and for that `T` one of these holds:
  - **G2b (name path).** `norm(owner)` occurs in `norm(owner_quote)` as whole words: no
    letter or digit right before or after it. Owner "علی" is unset when the quote only
    has "علیرضا".
  - **G2a (speaker path).** The item is a `commitment` and `owner` equals `T`'s speaker
    label exactly. A first-person promise ("I will send it") names its speaker. A
    `next_step` never uses this path.
  - **G2c.** The speaker path never grounds when `T`'s speaker label is the unknown
    label (`UNKNOWN_SPEAKER_LABEL`, `speaker_unknown`): that label names no person. An
    owner equal to it is unset unless the name path holds.

  The kept owner is the provider's text, unchanged. Otherwise the owner is unset.
- **G3.** The due date is kept only when: `due_date` is set; `due_quote` is not blank;
  `norm(due_quote)` is not empty and is inside `norm(T.text)` for a cited turn `T`; and
  `extract_temporal(due_quote, reference)` (`speech/dates.py`) returns exactly one match
  of kind `date` (**G3b**), that match is not ambiguous, and its value equals
  `due_date`. Two date matches leave the date unset, even when both are the same day
  ("فردا، یعنی ۱۴۰۵/۰۸/۲۲"). A `date_range` or a time next to one date match does not
  count ("هفته بعد، یعنی شنبه آینده" keeps the Saturday). A range alone, a time alone,
  an ambiguous match or another date leaves the date unset.
- **G4.** `kind` is the provider's label. The owner and date rules do not depend on it,
  except G2a and G2c.
- **G5.** The function is pure. It reads no database, no clock (only `reference`) and no
  network, and it never raises on a valid `ActionItem` and turn list. An unknown
  language uses the Persian normalizer. A naive `reference` is read as UTC.

### Storing the actions (`jobs/processor.py`, `persist_analysis`)

- **D3.** Each provider action is grounded against `model_turns` before it is cited.
  `reference` is the meeting's `started_at` when set, else its `created_at` (a naive
  value is read as UTC).
- A kept action is stored as before, with `assignee_text = grounded.owner`,
  `due_at = grounded.due_date` at 00:00 UTC, and `kind = grounded.kind`. Its evidence
  cites the provider's original `source_turn_indexes`, resolved against the analysed
  revision's segments as before (Amendment 2). So an out-of-range reference is still
  counted in the run's `rejected_reference_count` and the audit's
  `rejected_references`, and the action's `evidence_status` is `partial` when some
  references were rejected.
- A dropped action (G1) writes no row and no evidence span. Its references are still
  counted: the plan cites it with no segments and every offered reference rejected.
  `EvidencePlan` is unchanged.
- The pipeline never writes `assignee_user_id`. ADR 0054 A1 to A6 stay true: on a
  voicemail meeting a re-analysis keeps every AI action whose `assignee_user_id` is set,
  every column unchanged, and replaces only the others. `created_by` stays `ai`.

### The kind column (`database/saas_models.py`, migration `c4a8e2f7d913`)

- `action_items.kind` is a nullable `VARCHAR(16)` with
  `ck_action_kind: kind IS NULL OR kind IN ('commitment', 'next_step')`.
- The pipeline sets it on every AI action written from now on. A person's action
  (`created_by = 'user'`), a voicemail rule action and every older row stay NULL. There
  is no backfill.
- On SQLite the migration adds the column with its own named CHECK and drops it again in
  place, so the table is not rebuilt. On PostgreSQL it adds the column and the check.
  No row-level security or grant changes: the column belongs to an existing table.

### The API (`schemas/insights.py`)

- `ActionItemResponse.kind` (string or null). `GET /api/v1/meetings/{id}/actions`
  returns it. The create and update requests do not accept it (`extra="forbid"`). The
  published contract `contracts/ingestion-v1.openapi.json` gains this one field.

### The OpenAI-compatible provider (`providers/openai_compatible.py`)

- Each item of `action_items` in the strict schema requires `kind` (`commitment` or
  `next_step`), `owner_quote` and `due_quote` (string or null).
- The prompt says: an action item is something a speaker says will be done; `kind` is
  `commitment` when a speaker promises or accepts to do it and `next_step` when it is
  only proposed; set the owner only when the turns name who will do it, and copy the
  naming words into `owner_quote`; set the due date only when a speaker states a day or
  date, and copy the words into `due_quote`; otherwise null; never guess.
- `PROMPT_VERSION` is 5. The mock provider still returns no action items.

### Measuring it (`evaluation/action_items.py`)

`evaluate_action_items` scores cases of gold against stored values:
`owner_precision`, `due_precision`, `owner_unset_rate`, `due_unset_rate`,
`kind_accuracy` and `keep_precision` (see `evaluation/README.md`).

- The dev gate `tests/test_action_grounding_eval.py` grounds the fixed provider outputs
  of `tests/fixtures/evaluation/action_items/dev.jsonl` (52 cases, 42 Persian, 10
  English). It needs owner and due precision of at least 0.95 and both unset rates
  at 1.0.
- The held-out set of F38 is `heldout.jsonl` in the same folder, written blind by the
  reviewer from the contract alone, with its own oracle test. It needs the same 0.95.

What the number measures: the grounding guard over fixed provider outputs, including
wrong ones (invented owners and dates, wrong quotes, out-of-range turns). It does not
measure a real model. No language model runs in production yet; a real model is measured
later with the HOM-351 local model, through the same evaluator.

## Known limits

- G2 does not check the grammatical role of a name in the quote. "با تأمین‌کننده صحبت
  کند" grounds owner "تأمین‌کننده" although that word is the other party.
- The quote-in-turn check is a plain substring check. A quote cut from a longer phrase
  still passes: "رضا گزارش رو" cut from "علیرضا گزارش رو" (the whole-word rule then
  checks the name inside the quote only), or "فردا" cut from "فردا یا پس‌فردا".
- `normalize_for_matching` removes the ZWNJ but keeps a space. A quote that spells
  "پس فردا" with a space does not match a turn that writes "پس‌فردا". This loses
  recall only.
- `extract_temporal` reads Persian and ISO dates only. An English relative date ("by
  tomorrow") is never grounded.

## Not built

- No approval workflow for extracted actions (HOM-135).
- No change in the product console.
- No English relative dates.
- No reviewer edit of `kind`.

## Consequences

- A reviewer sees an owner or a due date only when the call states it. Fewer actions
  carry an owner or a date than before.
- An action with no valid turn reference is no longer stored.
- Each analysis prompt change bumps `PROMPT_VERSION`, so cached analyses from version 4
  are not reused.
