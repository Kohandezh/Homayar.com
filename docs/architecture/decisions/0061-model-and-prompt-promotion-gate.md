# ADR 0061: Model and prompt promotion gate

Date: 2026-11-12 · Status: Proposed (HOM-71, WP13-T07, F81) · Builds on: ADR 0056

All code is under `Homayar/backend/evaluation/`: `promotion.py` (the gate, the ledger
and the CLI), `promotion_formats.py` (the shared format validators), `corrections.py`
and a small guard in `loaders.py`. Pure Python and files only: no database, no API
route, no migration, no console page.

## Context

A model or prompt change must not reach production without a held-out quality, slice and
cost report, and an approved rollback manifest (F81, parent HOM-17). The evaluation
command (HOM-69) and the candidate report (HOM-70) already exist. What was missing: a
gate that reads those reports against frozen release criteria, a kept record of what was
promoted and how to undo it, and a place for production corrections that can never leak
back into an evaluation corpus. The design source is
`docs/planning/work-packages/WP13/annotation-protocol.md` sections 10 and 11.

## Decision

### D1. Formats, all JSON with unknown keys rejected

- `release-criteria/1`: written by an operator and frozen. Holds the gates (evaluation,
  metric, slice, direction, threshold, frozen held-out size, max regression, tolerance),
  the default tolerance, the minimum number of reproduction runs, the approver roles and
  the measurement limits (latency, cost, currency, single provider).
- `rollback-manifest/1`: what to restore if the promotion goes wrong: the previous
  version and config, the report hashes it was judged on, the triggers that fire a
  rollback, and the approval (who, role, when).
- Promotion request: the file paths and the pinned sha256 of the criteria file.
- `promotion-decision/1`: the gate output. `promote` exactly when the reason list is
  empty, else `blocked` with every reason.
- Promotion ledger: one JSON object per line, append only. A promotion entry carries the
  full rollback manifest; a rollback entry names the promotion it undoes.
- `production-correction/1`: one supervised fix (before and after) with opaque workspace
  and subject references.

The open owner decisions (approver role, tolerance, rollback trigger values, freeze
date) live in the criteria and manifest files, not in code. The code enforces their
presence and shape.

### D2. The gate collects every reason

`python -m backend.evaluation.promotion check <request.json>` runs all checks and never
stops at the first failure. Each reason has a code and a detail; the top list is sorted
by code then detail. Exit 0 for `promote`, 2 for `blocked`, 1 for input errors (bad
JSON, unknown key, missing file, wrong shape) with one `error:` line on stderr. Equal
values pass: a value exactly at the threshold, a regression exactly at `max_regression`,
a reproduction difference exactly at tolerance.

The reason codes, in check order:

| Code | Check |
| --- | --- |
| `criteria_changed` | A1: the criteria file bytes no longer hash to the pinned sha |
| `report_version_mismatch` | A2: a report is not `candidate-report/1` |
| `evaluation_version_mismatch` | A2: a report's evaluation version differs from the criteria's |
| `no_change` | A3: the incumbent is the candidate (same name and config sha) |
| `gate_missing` | A4: the evaluation, slice or metric is absent, or its value is null |
| `held_out_size_mismatch` | A5 and A8: a slice size differs from the frozen `n_items` |
| `insufficient_sample` | A6: the candidate slice is flagged insufficient |
| `threshold_missed` | A7: the value is on the wrong side of the threshold |
| `incumbent_gate_missing` | A8: the incumbent value is absent or null |
| `regression` | A9: the candidate regresses more than `max_regression` |
| `reproduction_missing` | A10: fewer reproduction reports than `min_reproductions` |
| `reproduction_mismatch` | A11: a reproduction names another candidate or config |
| `not_reproducible` | A12: a reproduction value is missing, its slice size differs, or it differs from the candidate by more than the tolerance |
| `measurements_missing` | A13: measurements are required but not measured, or the slice is absent |
| `latency_over` | A14: p95 latency is null or over the limit |
| `cost_over` | A15: mean cost per request is null or over the limit |
| `currency_mismatch` | A15: the report currency differs from the criteria currency |
| `provider_mixing` | A16: more than one provider, or a request without one |
| `rollback_manifest_missing` | A17: the request has no rollback manifest |
| `rollback_manifest_invalid` | A17: the manifest file fails the `rollback-manifest/1` shape |
| `rollback_manifest_mismatch` | A18: one reason per manifest field that does not match the reports |
| `rollback_manifest_not_approved` | A19: no approval, or the approver role is not allowed |

### D3. The ledger is the rollback manifest that is kept

`record` runs `check` first: a blocked decision leaves the ledger untouched. It also
refuses a stale incumbent: when the ledger has an active version, the request's
incumbent report must match it. A promotion entry stores the sha256 of the printed
decision text, so the exact judgement is replayable. `rollback` appends an entry that
undoes the top promotion and restores the previous version and config from its manifest;
it refuses when the stack is empty or there is nothing to restore. `active` prints the
version in effect. State is a stack of promotions; a promotion pushes, a rollback pops.

### D4. Corrections are refused by the loaders

Production corrections (transcript revisions, review fixes) are stored in their own
append-only JSONL files, never in the corpus directories. `loaders.parse_record` raises
for any record whose `origin` is `production_correction`, before kind parsing, so
`load_jsonl`, the evaluation CLI and the candidate report all refuse a data file that
holds one. An evaluation record with another `origin` value (for example `gold`) loads
exactly as before. `append` validates every input record first and writes nothing on any
error, and refuses (exit 2) to place the store inside any `--corpus-root`, compared
after `os.path.realpath`.

## Known limits

- The held-out identity of a slice is pinned by its size only (`n_input_items` against
  the frozen `n_items`) until the F80 split manifest exists (WP13-T03).
- The gate trusts the operator that each reproduction report comes from an independent
  run: a request may list the candidate report itself as a reproduction, and the gate
  cannot tell.
- The ledger has no file lock: one operator at a time.
- The database export of production corrections (transcript revisions, review decisions)
  and the API and console page (HOM-234) are later work.
