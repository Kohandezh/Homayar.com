# ADR 0048: Stored trend judgments

Date: 2026-10-30 · Status: Proposed (HOM-290) · Builds on: ADR 0043

All code paths are under `Homayar/backend/`.

## Context

The trend routes judge every conversation of the window from its stored extraction JSON with
the HOM-79 rules (`issue_trends/counting.py`). ADR 0043 keeps those judgments in a cache per
worker process. A cold worker (after a deploy, or for a workspace it has not seen) still reads
and judges every row, and each worker holds its own copy.

What a stored extraction row decides (`JudgedContent`) depends only on that row, which never
changes, and on the rules: the trend definition (`definition.digest`) and `JUDGMENT_VERSION`.
ADR 0043 named a stored table as option 4.

## Decision

1. **The table.** `trend_extraction_judgments` (migration `f2c6a8e4b1d7`, HOM-290 WU1): one
   immutable row per analysis run and rules version, unique on (`analysis_run_id`,
   `definition_digest`, `judgment_version`). It holds ids, versions and `content_key` only, no
   personal data. Tenant-only row-level security, like `analysis_extractions`. Rows go with
   their extraction, their run and their workspace (`ON DELETE CASCADE`).
2. **The key.** `content_key` is the 64-character hex SHA-256 of the canonical JSON of the
   run's `JudgedContent` (`services/trend_judgment_store.py`, `content_key`): the dataclass as
   plain JSON (enum values as strings, the judgments as lists in their order), sorted keys,
   no spaces, ASCII. Equal contents have equal keys. The key is not a code of the rules: a
   reader never decodes it.
3. **Write.** `persist_analysis` (`jobs/processor.py`) writes the row with the extraction
   row, in the same transaction, for the full job and the HOM-63 recompute. It judges with
   `SignatureJudge.content`, the only rules source. It writes in a savepoint: a failure loses
   the row only, never the analysis, and logs `TREND_JUDGMENT_SKIPPED` with the run id and
   the error class.
4. **Read.** Statement P of the trend routes joins the workspace's rows of the current
   definition digest and `JUDGMENT_VERSION`, read once as one index-only range of
   `ix_trend_judgment_current` and hash-joined (a `MATERIALIZED` CTE). A run with a row needs
   no JSON. The rows are grouped by key; one more statement (S) reads one representative
   extraction per distinct key (the smallest run id), judged once. Its content is used for
   every run of the key: the key is trusted. The conversation-level facts (no run, stale,
   mock, no row, counted copy, duplicate media) are still decided per conversation by
   `SignatureJudge.judge`. S runs only when P found a row, so a route judges with at most one
   more statement per workspace population.
5. **Fallback.** A run with no row of the current rules is judged as before: the ADR 0043
   cache and its warm-up stay (the warm-up skips runs with a row). So answers are the same
   before the backfill, during it, after a definition change and after a `JUDGMENT_VERSION`
   bump. Tests compare every answer with no rows, all rows and a mix.
6. **Backfill.** `uv run python -m backend.services.trend_judgment_store backfill
   [--workspace ID] [--batch-size N]` writes the missing rows of the current runs (keyset
   batches, one commit per batch). It prints counts only. Running it again writes nothing; a
   row written meanwhile by the application is skipped (`ON CONFLICT DO NOTHING` on the
   unique key). Without `--workspace` it walks every workspace that has a processing job.

## Operations

- **Run the backfill after a deploy that changes the trend definition or
  `JUDGMENT_VERSION`.** Until it has run, those conversations are judged through the
  fallback: correct, but as slow as before HOM-290.
- Rows of an older definition or version are never read. Nothing deletes them yet; they cost
  disk and index space only.
- A manual SQL change to a stored row changes answers (the key is trusted). Rows are written
  only by the application.

## Consequences

- A cold worker answers as fast as a warm one for every run with a row.
- One more statement per judged workspace population when rows exist (the statement-count
  test checks it with rows: it does not grow with the data).
- The table grows by one row per analysis run and rules version; `ix_trend_judgment_current`
  is about the size of the unique key's index plus the key column.
